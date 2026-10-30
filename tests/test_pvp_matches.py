import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import g
from app_with_auth import app
from models import User, db
from pvp.match_logic import advance_match_clock, safe_progress_rows
from pvp.models import (
    PvPLobby,
    PvPLobbyMember,
    PvPMatch,
    PvPMatchPlayer,
    PvPMatchRound,
)
from pvp.presence import OFFLINE_AFTER_SECONDS, cleanup_stale_lobbies
from word_dictionary import EN_US_DICTIONARY


class PvPMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".pvp-matches-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="pvp-match-test-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{cls.database_path}",
        )

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def create_user(self, number):
        user = User(
            google_id=f"match-google-{number}",
            email=f"match-{number}@example.test",
            name=f"Match Player {number}",
            picture=f"https://example.test/avatar-{number}.png",
        )
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, client, user):
        g.pop("_login_user", None)
        with client.session_transaction() as saved:
            saved["_user_id"] = str(user.id)
            saved["_fresh"] = True

    def csrf(self, client):
        g.pop("_login_user", None)
        client.get("/pvp/")
        with client.session_transaction() as saved:
            return saved["pvp_csrf_token"]

    def post(self, client, path, **fields):
        token = self.csrf(client)
        g.pop("_login_user", None)
        return client.post(path, data={"csrf_token": token, **fields}, follow_redirects=False)

    def post_existing(self, client, path, **fields):
        with client.session_transaction() as saved:
            token = saved["pvp_csrf_token"]
        g.pop("_login_user", None)
        return client.post(path, data={"csrf_token": token, **fields}, follow_redirects=False)

    def create_lobby(self, leader, guests=(), words=5, mode="timer"):
        clients = [app.test_client() for _ in range(1 + len(guests))]
        self.login(clients[0], leader)
        for client, guest in zip(clients[1:], guests):
            self.login(client, guest)
        self.post(clients[0], "/pvp/create")
        lobby = PvPLobby.query.one()
        for client in clients[1:]:
            self.post(client, f"/pvp/{lobby.public_id}/join")
        if words != 5 or mode != "timer":
            self.post(
                clients[0], f"/pvp/{lobby.public_id}/settings",
                words_per_match=str(words), mode=mode,
                timer_seconds="90" if mode == "timer" else "",
            )
        for client in clients:
            self.post(client, f"/pvp/{lobby.public_id}/ready", ready="true")
        return lobby, clients

    def start(self, leader_client, lobby, now=None):
        with patch("pvp.routes._utcnow", return_value=now or datetime.utcnow()):
            return self.post(leader_client, f"/pvp/{lobby.public_id}/start")

    def create_match(self, leader_num=1, guest_nums=(2,), words=5, mode="timer"):
        leader = self.create_user(leader_num)
        guests = [self.create_user(num) for num in guest_nums]
        lobby, clients = self.create_lobby(leader, guests, words, mode)
        now = datetime.utcnow()
        response = self.start(clients[0], lobby, now)
        self.assertEqual(response.status_code, 302)
        return lobby, clients, now

    def test_leader_creates_single_countdown_match_with_unique_dictionary_sequence(self):
        lobby, clients, now = self.create_match(words=10)
        match = PvPMatch.query.one()
        rounds = PvPMatchRound.query.filter_by(match_id=match.id).order_by(PvPMatchRound.round_number).all()
        self.assertEqual(match.status, "countdown")
        self.assertEqual(lobby.status, "matched")
        self.assertEqual(match.words_count, 10)
        self.assertEqual(match.attempts_per_word, 6)
        self.assertEqual(match.countdown_started_at, now)
        self.assertEqual(match.scheduled_start_at, now + timedelta(seconds=5))
        self.assertEqual(len(rounds), 10)
        words = [round_.word for round_ in rounds]
        self.assertEqual(len(set(words)), 10)
        self.assertTrue(all(EN_US_DICTIONARY.contains(word) for word in words))
        self.assertEqual(PvPMatchPlayer.query.filter_by(match_id=match.id).count(), 2)
        self.assertEqual([(item.current_round, item.attempts_used, item.progress) for item in match.players], [(1, 0, "[]"), (1, 0, "[]")])
        repeat = self.start(clients[0], lobby, now + timedelta(seconds=2))
        self.assertEqual(repeat.headers["Location"], f"/pvp/match/{match.public_id}")
        self.assertEqual(PvPMatch.query.count(), 1)
        self.assertEqual(PvPMatch.query.one().countdown_started_at, now)

    def test_nonleader_cannot_create_match_and_not_ready_member_blocks_start(self):
        leader, guest = self.create_user(3), self.create_user(4)
        lobby, clients = self.create_lobby(leader, [guest])
        nonleader = self.post(clients[1], f"/pvp/{lobby.public_id}/start")
        self.assertEqual(PvPMatch.query.count(), 0)
        guest_membership = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=guest.id).one()
        guest_membership.is_ready = False
        db.session.commit()
        blocked = self.post(clients[0], f"/pvp/{lobby.public_id}/start")
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(lobby.status, "open")
        self.assertEqual(PvPMatch.query.count(), 0)

    def test_offline_member_blocks_start_until_existing_presence_cleanup_removes_them(self):
        leader, guest = self.create_user(5), self.create_user(6)
        lobby, clients = self.create_lobby(leader, [guest])
        membership = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=guest.id).one()
        now = datetime.utcnow()
        membership.last_seen_at = now - timedelta(seconds=OFFLINE_AFTER_SECONDS + 1)
        db.session.commit()

        with patch("pvp.routes._utcnow", return_value=now):
            response = self.post(clients[0], f"/pvp/{lobby.public_id}/start")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PvPMatch.query.count(), 0)
        self.assertEqual(lobby.status, "open")
        self.assertIsNotNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=guest.id).first())

        self.post_existing(clients[0], f"/pvp/{lobby.public_id}/heartbeat")
        self.assertIsNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=guest.id).first())
        self.post_existing(clients[0], f"/pvp/{lobby.public_id}/ready", ready="true")
        started = self.post_existing(clients[0], f"/pvp/{lobby.public_id}/start")
        self.assertEqual(started.status_code, 302)
        self.assertEqual(PvPMatch.query.count(), 1)

    def test_countdown_is_stable_on_reload_and_get_state_is_read_only(self):
        lobby, clients, now = self.create_match(leader_num=7, guest_nums=(8,))
        match = PvPMatch.query.one()
        match_url = f"/pvp/match/{match.public_id}"
        page_before = clients[0].get(match_url)
        page_after = clients[0].get(match_url)
        self.assertEqual(page_before.status_code, 200)
        self.assertEqual(page_after.status_code, 200)
        self.assertNotIn(match.rounds[0].word.encode(), page_after.data)
        self.assertEqual(match.countdown_started_at, now)
        response = clients[0].get(match_url + "/state")
        payload = response.get_json()
        self.assertEqual(payload["status"], "countdown")
        self.assertEqual(payload["countdown_seconds_remaining"], 5)
        self.assertIsNone(payload["round"]["word"])
        self.assertNotIn("rounds", payload)
        self.assertNotIn(match.rounds[1].word, json.dumps(payload))

    def test_post_state_materializes_one_synchronized_start_and_timer_deadline(self):
        lobby, clients, now = self.create_match(leader_num=9, guest_nums=(10,))
        match = PvPMatch.query.one()
        endpoint = f"/pvp/match/{match.public_id}/state"
        due = match.scheduled_start_at
        with patch("pvp.routes._utcnow", return_value=due):
            first = self.post_existing(clients[0], endpoint)
        self.assertEqual(first.status_code, 200)
        payload = first.get_json()
        self.assertEqual(payload["status"], "in_progress")
        self.assertEqual(payload["started_at"], due.isoformat(timespec="milliseconds") + "Z")
        self.assertEqual(payload["round"]["started_at"], payload["started_at"])
        self.assertEqual(payload["round"]["deadline_at"], (due + timedelta(seconds=120)).isoformat(timespec="milliseconds") + "Z")
        word = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().word
        self.assertEqual(payload["round"]["word"], word)
        self.assertNotIn(PvPMatchRound.query.filter_by(match_id=match.id, round_number=2).one().word, json.dumps(payload))
        with patch("pvp.routes._utcnow", return_value=due + timedelta(seconds=1)):
            second = self.post_existing(clients[1], endpoint)
        self.assertEqual(second.get_json()["started_at"], payload["started_at"])
        self.assertEqual(second.get_json()["round"]["word"], payload["round"]["word"])
        lobby_state = clients[1].get(f"/pvp/{lobby.public_id}/state").get_json()
        self.assertEqual(lobby_state["match_url"], f"/pvp/match/{match.public_id}")
        self.assertEqual(PvPMatch.query.one().started_at, due)

    def test_no_limit_round_has_no_deadline_and_does_not_expire(self):
        _, clients, _ = self.create_match(leader_num=11, guest_nums=(12,), words=3, mode="no_limit")
        match = PvPMatch.query.one()
        self.assertIsNone(match.timer_seconds)
        round_ = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one()
        due = match.scheduled_start_at
        with patch("pvp.routes._utcnow", return_value=due):
            self.post_existing(clients[0], f"/pvp/match/{match.public_id}/state")
        db.session.refresh(round_)
        self.assertEqual(round_.status, "in_progress")
        self.assertIsNone(round_.deadline_at)
        self.assertFalse(advance_match_clock(match, due + timedelta(days=1)))
        self.assertEqual(round_.status, "in_progress")

    def test_round_deadline_expires_idempotently_on_next_post_state(self):
        _, clients, _ = self.create_match(leader_num=13, guest_nums=(14,))
        match = PvPMatch.query.one()
        endpoint = f"/pvp/match/{match.public_id}/state"
        due = match.scheduled_start_at
        with patch("pvp.routes._utcnow", return_value=due):
            self.post_existing(clients[0], endpoint)
        round_ = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one()
        deadline = due + timedelta(seconds=120)
        with patch("pvp.routes._utcnow", return_value=deadline):
            get_state = clients[0].get(endpoint)
            self.assertEqual(get_state.get_json()["round"]["status"], "expired")
            self.assertEqual(round_.status, "in_progress")  # GET does not mutate.
            response = self.post_existing(clients[0], endpoint)
            self.assertEqual(response.get_json()["round"]["status"], "expired")
            self.assertEqual(round_.status, "expired")
            self.assertFalse(advance_match_clock(match, deadline + timedelta(seconds=1)))

    def test_no_late_join_or_lobby_settings_change_after_match_creation(self):
        leader, guest, outsider = self.create_user(15), self.create_user(16), self.create_user(17)
        lobby, clients = self.create_lobby(leader, [guest])
        self.post(clients[0], f"/pvp/{lobby.public_id}/start")
        outsider_client = app.test_client()
        self.login(outsider_client, outsider)
        self.post(outsider_client, f"/pvp/{lobby.public_id}/join")
        self.assertIsNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=outsider.id).first())
        self.post(clients[0], f"/pvp/{lobby.public_id}/settings", words_per_match="100", mode="timer", timer_seconds="90")
        db.session.refresh(lobby)
        self.assertEqual(lobby.words_per_match, 5)
        self.assertEqual(PvPMatch.query.count(), 1)

    def test_offline_match_player_and_color_only_progress_survive_and_can_reconnect(self):
        lobby, clients, _ = self.create_match(leader_num=18, guest_nums=(19,))
        match = PvPMatch.query.one()
        player = PvPMatchPlayer.query.filter(PvPMatchPlayer.user_id != lobby.leader_id).one()
        player.progress = json.dumps([["green", "yellow", "gray", "green", "gray"]])
        player.attempts_used = 1
        player.last_seen_at = datetime.utcnow() - timedelta(seconds=OFFLINE_AFTER_SECONDS + 1)
        db.session.commit()
        cleanup_stale_lobbies(now=datetime.utcnow() + timedelta(seconds=OFFLINE_AFTER_SECONDS + 2))
        db.session.commit()
        self.assertIsNotNone(db.session.get(PvPLobby, lobby.id))
        self.assertIsNotNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.user_id).first())
        self.assertIsNotNone(db.session.get(PvPMatchPlayer, player.id))

        state = clients[1].get(f"/pvp/match/{match.public_id}/state").get_json()
        public_player = next(item for item in state["players"] if item["name"] == player.user.name)
        self.assertFalse(public_player["is_online"])
        self.assertEqual(public_player["progress"], [["green", "yellow", "gray", "green", "gray"]])
        self.assertNotIn("word", public_player)
        self.assertNotIn("attempt", json.dumps(public_player).lower().replace("attempts_used", ""))

        reconnect = self.post_existing(clients[1], f"/pvp/match/{match.public_id}/state")
        self.assertTrue(reconnect.get_json()["players"][1]["is_online"])
        self.assertEqual(PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=player.user_id).count(), 1)

    def test_outsider_cannot_access_match_page_or_state(self):
        lobby, _, _ = self.create_match(leader_num=20, guest_nums=(21,))
        match = PvPMatch.query.one()
        outsider = self.create_user(22)
        client = app.test_client()
        self.login(client, outsider)
        self.assertEqual(client.get(f"/pvp/match/{match.public_id}").status_code, 404)
        self.assertEqual(client.get(f"/pvp/match/{match.public_id}/state").status_code, 404)
        self.assertEqual(client.post(f"/pvp/match/{match.public_id}/state").status_code, 400)

    def test_progress_parser_never_returns_guess_text_or_invalid_rows(self):
        raw = json.dumps([
            ["green", "yellow", "gray", "gray", "green"],
            ["CRANE", "A", "B", "C", "D"],
            ["green", "x", "gray", "gray", "green"],
            "crane",
        ])
        self.assertEqual(safe_progress_rows(raw), [["green", "yellow", "gray", "gray", "green"]])


if __name__ == "__main__":
    unittest.main()
