import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from flask import g
from app_with_auth import app
from models import User, db
from pvp.match_logic import advance_match_clock, safe_progress_rows
from pvp.models import (
    PvPLobby,
    PvPLobbyMember,
    PvPMatch,
    PvPMatchAttempt,
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

    def start_first_round(self, match, client, now=None):
        due = match.scheduled_start_at
        with patch("pvp.routes._utcnow", return_value=now or due):
            response = self.post_existing(client, f"/pvp/match/{match.public_id}/state")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def guess(self, client, match, word, request_id=None, **extra):
        return self.post_existing(
            client,
            f"/pvp/match/{match.public_id}/guess",
            guess=word,
            request_id=request_id or str(uuid4()),
            **extra,
        )

    def leave_match(self, client, match):
        return self.post_existing(client, f"/pvp/match/{match.public_id}/leave")

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
        db.session.add(PvPMatchAttempt(
            match_id=match.id,
            player=player,
            round=match.rounds[0],
            attempt_number=1,
            request_id=str(uuid4()),
            guess="CRANE",
            colors=json.dumps(["green", "yellow", "gray", "green", "gray"]),
            submitted_at=datetime.utcnow(),
        ))
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
        denied_guess = self.post(client, f"/pvp/match/{match.public_id}/guess", guess="array", request_id=str(uuid4()))
        self.assertEqual(denied_guess.status_code, 404)

    def test_progress_parser_never_returns_guess_text_or_invalid_rows(self):
        raw = json.dumps([
            ["green", "yellow", "gray", "gray", "green"],
            ["CRANE", "A", "B", "C", "D"],
            ["green", "x", "gray", "gray", "green"],
            "crane",
        ])
        self.assertEqual(safe_progress_rows(raw), [["green", "yellow", "gray", "gray", "green"]])

    def test_correct_guess_is_saved_and_completes_only_that_player(self):
        lobby, clients, _ = self.create_match(leader_num=23, guest_nums=(24,))
        match = PvPMatch.query.one()
        round_ = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one()
        round_.word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])

        response = self.guess(clients[0], match, "CIGAR", colors="gray,gray,gray,gray,gray", attempt_number="999")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["attempt_number"], 1)
        self.assertEqual(payload["colors"], ["green"] * 5)
        self.assertEqual(payload["my_status"], "solved")
        attempt = PvPMatchAttempt.query.one()
        self.assertEqual((attempt.guess, attempt.attempt_number, attempt.is_correct), ("CIGAR", 1, True))
        player = PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=lobby.leader_id).one()
        self.assertEqual((player.attempts_used, player.status, player.has_solved), (1, "solved", True))
        self.assertEqual(round_.status, "in_progress")  # The other player still has the round.
        rejected = self.guess(clients[0], match, "array")
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(PvPMatchAttempt.query.filter_by(player_id=player.id).count(), 1)
        guest_result = self.guess(clients[1], match, "CIGAR")
        self.assertEqual(guest_result.status_code, 200)
        self.assertEqual(PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().status, "completed")
        self.assertEqual(match.current_round, 1)  # No automatic transition to the next word.

    def test_duplicate_letters_are_scored_using_remaining_answer_counts(self):
        _, clients, _ = self.create_match(leader_num=25, guest_nums=(26,))
        match = PvPMatch.query.one()
        PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])

        response = self.guess(clients[0], match, "array")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["colors"], ["gray", "yellow", "gray", "green", "gray"])

    def test_invalid_words_are_not_saved_or_counted_and_guess_length_is_enforced(self):
        _, clients, _ = self.create_match(leader_num=27, guest_nums=(28,))
        match = PvPMatch.query.one()
        self.start_first_round(match, clients[0])

        for guess, message in (("zzzzz", "Not in word list"), ("abc", "5 letters")):
            response = self.guess(clients[0], match, guess)
            self.assertEqual(response.status_code, 400)
            self.assertIn(message, response.get_json()["message"])
        player = PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=match.players[0].user_id).one()
        self.assertEqual(player.attempts_used, 0)
        self.assertEqual(PvPMatchAttempt.query.count(), 0)

    def test_idempotent_retry_returns_saved_result_and_conflicting_reuse_is_rejected(self):
        _, clients, _ = self.create_match(leader_num=29, guest_nums=(30,))
        match = PvPMatch.query.one()
        PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])
        request_id = str(uuid4())

        first = self.guess(clients[0], match, "array", request_id)
        retry = self.guess(clients[0], match, "array", request_id)
        conflict = self.guess(clients[0], match, "crane", request_id)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["duplicate"])
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.get_json()["duplicate"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(PvPMatchAttempt.query.count(), 1)

    def test_public_progress_exposes_colors_but_only_the_current_player_sees_guess_text(self):
        lobby, clients, _ = self.create_match(leader_num=31, guest_nums=(32,))
        match = PvPMatch.query.one()
        PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])
        self.guess(clients[0], match, "array")

        other_state = clients[1].get(f"/pvp/match/{match.public_id}/state").get_json()
        public_player = next(item for item in other_state["players"] if item["name"] == "Match Player 31")
        self.assertEqual(public_player["progress"], [["gray", "yellow", "gray", "green", "gray"]])
        self.assertNotIn("guess", public_player)
        self.assertNotIn("array", json.dumps(public_player).lower())
        self.assertNotIn("array", json.dumps(other_state["players"]).lower())
        own_state = clients[0].get(f"/pvp/match/{match.public_id}/state").get_json()
        self.assertEqual(own_state["my_attempts"][0]["guess"], "ARRAY")
        self.assertEqual(own_state["my_attempts"][0]["colors"], public_player["progress"][0])
        self.assertEqual(clients[0].get(f"/pvp/match/{match.public_id}").status_code, 200)

    def test_six_failed_guesses_end_player_round_and_seventh_is_blocked(self):
        _, clients, _ = self.create_match(leader_num=33, guest_nums=(34,))
        match = PvPMatch.query.one()
        PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])

        for guess in ("array", "crane", "apple", "adore", "agent", "other"):
            response = self.guess(clients[0], match, guess)
            self.assertEqual(response.status_code, 200, guess)
        player = PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=match.players[0].user_id).one()
        self.assertEqual((player.attempts_used, player.status), (6, "failed"))
        self.assertEqual(self.guess(clients[0], match, "abbey").status_code, 409)
        self.assertEqual(PvPMatchAttempt.query.filter_by(player_id=player.id).count(), 6)

    def test_timer_deadline_rejects_guess_and_marks_unfinished_player_expired(self):
        _, clients, _ = self.create_match(leader_num=35, guest_nums=(36,))
        match = PvPMatch.query.one()
        round_ = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one()
        round_.word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])
        deadline = round_.deadline_at
        with patch("pvp.routes._utcnow", return_value=deadline):
            response = self.guess(clients[0], match, "array")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "round_expired")
        self.assertEqual(PvPMatchAttempt.query.count(), 0)
        self.assertEqual(round_.status, "expired")
        player = PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=match.players[0].user_id).one()
        self.assertEqual(player.status, "expired")

    def test_no_time_limit_accepts_valid_guess_without_any_deadline(self):
        _, clients, _ = self.create_match(leader_num=39, guest_nums=(40,), mode="no_limit")
        match = PvPMatch.query.one()
        round_ = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one()
        round_.word = "cigar"
        db.session.commit()
        much_later = match.scheduled_start_at + timedelta(days=100)
        self.start_first_round(match, clients[0], much_later)
        self.assertIsNone(round_.deadline_at)
        with patch("pvp.routes._utcnow", return_value=much_later):
            response = self.guess(clients[0], match, "array")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PvPMatchAttempt.query.count(), 1)

    def test_two_simultaneous_guesses_cannot_exceed_six_attempt_limit(self):
        _, clients, _ = self.create_match(leader_num=37, guest_nums=(38,))
        match = PvPMatch.query.one()
        PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])
        for guess in ("array", "crane", "apple", "adore", "agent"):
            self.assertEqual(self.guess(clients[0], match, guess).status_code, 200)

        user_id = match.players[0].user_id
        concurrent_client = app.test_client()
        self.login(concurrent_client, db.session.get(User, user_id))
        self.csrf(concurrent_client)
        clients[0].get("/pvp/")
        with clients[0].session_transaction() as session_one:
            token_one = session_one["pvp_csrf_token"]
        with concurrent_client.session_transaction() as session_two:
            token_two = session_two["pvp_csrf_token"]
        barrier = Barrier(2)

        def send(client, token, guess):
            barrier.wait(timeout=5)
            return client.post(
                f"/pvp/match/{match.public_id}/guess",
                data={"csrf_token": token, "request_id": str(uuid4()), "guess": guess},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda args: send(*args),
                ((clients[0], token_one, "other"), (concurrent_client, token_two, "abbey")),
            ))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(PvPMatchAttempt.query.filter_by(player_id=match.players[0].id).count(), 6)

    def test_leave_is_available_in_all_match_and_round_states_and_is_idempotent(self):
        states = ("countdown", "in_progress", "round_completed", "round_expired", "completed")
        for index, target_state in enumerate(states):
            with self.subTest(state=target_state):
                # Each scenario needs an isolated roster; setUp only runs once for the outer test.
                db.session.remove()
                db.drop_all()
                db.create_all()
                leader_number = 60 + index * 2
                lobby, clients, _ = self.create_match(
                    leader_num=leader_number, guest_nums=(leader_number + 1,)
                )
                match = PvPMatch.query.one()
                player = PvPMatchPlayer.query.filter_by(
                    match_id=match.id, user_id=lobby.leader_id
                ).one()
                player.score = 12 + index
                db.session.commit()
                round_ = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one()
                if target_state != "countdown":
                    self.start_first_round(match, clients[0])
                if target_state == "round_completed":
                    round_.status = "completed"
                elif target_state == "round_expired":
                    round_.status = "expired"
                elif target_state == "completed":
                    match.status = "completed"
                    match.completed_at = datetime.utcnow()
                db.session.commit()

                with patch("pvp.routes._utcnow", return_value=datetime.utcnow()):
                    response = self.leave_match(clients[0], match)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], "/pvp/")
                db.session.refresh(player)
                frozen_at = player.score_frozen_at
                self.assertEqual(player.status, "left")
                self.assertEqual(player.score, 12 + index)
                self.assertIsNotNone(frozen_at)
                self.assertIsNone(PvPLobbyMember.query.filter_by(
                    lobby_id=lobby.id, user_id=player.user_id
                ).first())
                self.assertIsNotNone(db.session.get(PvPMatchPlayer, player.id))
                self.assertIsNotNone(db.session.get(PvPLobby, lobby.id))

                repeated = self.leave_match(clients[0], match)
                db.session.refresh(player)
                self.assertEqual(repeated.status_code, 302)
                self.assertEqual(player.score_frozen_at, frozen_at)
                self.assertEqual(player.score, 12 + index)
                with self.assertRaises(ValueError):
                    player.award_score(1)
                with self.assertRaises(ValueError):
                    player.score = player.score + 1

    def test_exit_keeps_attempt_history_other_players_and_finishes_round_without_left_player(self):
        lobby, clients, _ = self.create_match(leader_num=72, guest_nums=(73,))
        match = PvPMatch.query.one()
        round_ = PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one()
        round_.word = "cigar"
        db.session.commit()
        self.start_first_round(match, clients[0])
        saved_guess = self.guess(clients[0], match, "array")
        self.assertEqual(saved_guess.status_code, 200)
        # Resolve the other member through the fixed match roster, independent of mutable lobby membership.
        leaving_player = PvPMatchPlayer.query.filter_by(
            match_id=match.id, user_id=lobby.leader_id
        ).one()
        remaining_player = next(item for item in match.players if item.user_id != leaving_player.user_id)
        remaining_client = clients[1]
        solved = self.guess(remaining_client, match, "CIGAR")
        self.assertEqual(solved.status_code, 200)
        attempt_count = PvPMatchAttempt.query.filter_by(player_id=leaving_player.id).count()
        remaining_attempt_count = PvPMatchAttempt.query.filter_by(player_id=remaining_player.id).count()
        old_score = remaining_player.score

        exit_response = self.leave_match(clients[0], match)
        self.assertEqual(exit_response.status_code, 302)
        self.assertEqual(PvPMatchRound.query.filter_by(match_id=match.id, round_number=1).one().status, "completed")
        db.session.refresh(remaining_player)
        self.assertEqual(remaining_player.status, "solved")
        self.assertEqual(remaining_player.score, old_score)
        self.assertEqual(PvPMatchAttempt.query.filter_by(player_id=leaving_player.id).count(), attempt_count)
        self.assertEqual(PvPMatchAttempt.query.filter_by(player_id=remaining_player.id).count(), remaining_attempt_count)
        self.assertIsNotNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=remaining_player.user_id).first())
        public_state = self.post_existing(remaining_client, f"/pvp/match/{match.public_id}/state").get_json()
        left_public = next(item for item in public_state["players"] if item["name"] == "Match Player 72")
        self.assertEqual(left_public["round_status"], "left")
        self.assertFalse(left_public["is_online"])

    def test_left_player_old_tabs_cannot_refresh_presence_or_submit_after_exit(self):
        _, clients, _ = self.create_match(leader_num=74, guest_nums=(75,))
        match = PvPMatch.query.one()
        player = PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=match.players[0].user_id).one()
        self.leave_match(clients[0], match)
        db.session.refresh(player)
        last_seen = player.last_seen_at

        heartbeat = self.post_existing(clients[0], f"/pvp/match/{match.public_id}/state")
        self.assertEqual(heartbeat.status_code, 410)
        self.assertEqual(heartbeat.get_json()["error"], "match_left")
        guess = self.guess(clients[0], match, "array")
        self.assertEqual(guess.status_code, 409)
        self.assertEqual(guess.get_json()["error"], "player_left")
        db.session.refresh(player)
        self.assertEqual(player.status, "left")
        self.assertEqual(player.last_seen_at, last_seen)
        self.assertEqual(clients[0].get(f"/pvp/match/{match.public_id}").headers["Location"], "/pvp/")

    def test_leave_releases_user_to_create_or_join_another_lobby_without_old_match_redirect(self):
        old_lobby, clients, _ = self.create_match(leader_num=76, guest_nums=(77,))
        old_match = PvPMatch.query.one()
        self.leave_match(clients[0], old_match)
        self.assertEqual(clients[0].get("/pvp/").status_code, 200)
        created = self.post_existing(clients[0], "/pvp/create")
        self.assertEqual(created.status_code, 302)
        new_lobby = PvPLobbyMember.query.filter_by(user_id=old_lobby.leader_id).one().lobby
        self.assertNotEqual(new_lobby.id, old_lobby.id)
        self.assertIsNotNone(db.session.get(PvPMatch, old_match.id))
        self.post_existing(clients[0], f"/pvp/{new_lobby.public_id}/leave")

        joiner = self.create_user(78)
        join_client = app.test_client()
        self.login(join_client, joiner)
        joined_create = self.post(join_client, "/pvp/create")
        self.assertEqual(joined_create.status_code, 302)
        join_lobby = PvPLobbyMember.query.filter_by(user_id=joiner.id).one().lobby
        self.leave_match(clients[0], old_match)
        joined = self.post_existing(clients[0], f"/pvp/{join_lobby.public_id}/join")
        self.assertEqual(joined.status_code, 302)
        self.assertIsNotNone(PvPLobbyMember.query.filter_by(lobby_id=join_lobby.id, user_id=old_lobby.leader_id).first())

    def test_match_page_contains_csrf_protected_exit_form(self):
        _, clients, _ = self.create_match(leader_num=79, guest_nums=(80,))
        match = PvPMatch.query.one()
        page = clients[0].get(f"/pvp/match/{match.public_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Покинути матч".encode(), page.data)
        self.assertIn(f"/pvp/match/{match.public_id}/leave".encode(), page.data)
        self.assertIn(b'name="csrf_token"', page.data)


if __name__ == "__main__":
    unittest.main()
