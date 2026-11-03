import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import g

from app_with_auth import app
from models import User, db
from pvp.models import PvPLobby, PvPLobbyMember, PvPMatch, PvPMatchPlayer, PvPMatchResult


class PvPMatchHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".pvp-history-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="pvp-history-test-secret",
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

    def user(self, key, name=None):
        user = User(
            google_id=f"history-google-{key}",
            email=f"secret-{key}@example.test",
            name=name or f"History Player {key}",
            picture=f"https://example.test/{key}.png",
        )
        db.session.add(user)
        db.session.flush()
        return user

    def login(self, client, user):
        g.pop("_login_user", None)
        with client.session_transaction() as saved:
            saved["_user_id"] = str(user.id)
            saved["_fresh"] = True

    def make_match(self, key, users, completed_at, score_offset=0, with_progress=False, with_memberships=False):
        lobby = PvPLobby(leader_id=users[0].id, status="matched")
        db.session.add(lobby)
        db.session.flush()
        match = PvPMatch(
            lobby_id=lobby.id,
            status="completed",
            game_mode="timer",
            words_count=3,
            timer_seconds=90,
            attempts_per_word=6,
            current_round=3,
            created_at=completed_at - timedelta(minutes=4),
            countdown_started_at=completed_at - timedelta(minutes=4),
            scheduled_start_at=completed_at - timedelta(minutes=4),
            started_at=completed_at - timedelta(minutes=4),
            completed_at=completed_at,
        )
        db.session.add(match)
        db.session.flush()
        for index, user in enumerate(users):
            player = PvPMatchPlayer(
                match_id=match.id,
                user_id=user.id,
                status="completed",
                score=90 - (index * 10) + score_offset,
                round_history=(json.dumps([{
                    "round_number": 1,
                    "status": "solved",
                    "progress": [["green", "yellow", "gray", "green", "gray"]],
                }]) if with_progress and index == 0 else "[]"),
            )
            db.session.add(player)
            db.session.flush()
            db.session.add(PvPMatchResult(
                match_id=match.id,
                user_id=user.id,
                game_mode="timer",
                words_count=3,
                final_score=player.score,
                place=index + 1,
                is_winner=index == 0,
                words_solved=1,
                total_attempts=2,
                completed_words=3,
                green_points=20,
                yellow_points=10,
                time_bonus=5,
                word_solved_bonus=15,
                unused_attempt_bonus=12,
                participation_status="completed",
                score_breakdown="{}",
                finalized_at=completed_at,
            ))
        db.session.flush()
        if with_memberships:
            # Keep lobby membership until the active results window expires.
            for user in users:
                db.session.add(PvPLobbyMember(lobby_id=lobby.id, user_id=user.id))
        db.session.commit()
        return match

    def test_history_is_private_paginated_sorted_and_empty_state_is_clear(self):
        player = self.user("owner")
        opponent = self.user("opponent")
        outsider = self.user("outsider")
        base = datetime(2026, 10, 1)
        for index in range(22):
            self.make_match(f"m{index}", [player, opponent], base + timedelta(days=index))
        other_match = self.make_match("other-only", [outsider], base + timedelta(days=30))

        client = app.test_client()
        self.assertEqual(client.get("/pvp/history").status_code, 302)
        self.login(client, player)
        first = client.get("/pvp/history")
        self.assertEqual(first.status_code, 200)
        body = first.get_data(as_text=True)
        self.assertIn("Сторінка 1 з 2", body)
        self.assertIn("22.10.2026", body)
        self.assertEqual(client.get(f"/pvp/history/{other_match.id}").status_code, 404)
        self.assertEqual(client.get("/pvp/history?page=-3").status_code, 200)
        second = client.get("/pvp/history?page=2")
        self.assertIn("Сторінка 2 з 2", second.get_data(as_text=True))

        no_history_client = app.test_client()
        no_history_user = self.user("empty-history")
        self.login(no_history_client, no_history_user)
        self.assertIn("поки немає завершених PvP-матчів", no_history_client.get("/pvp/history").get_data(as_text=True))

    def test_history_detail_is_member_only_and_shows_snapshot_without_private_data(self):
        player, opponent, outsider = self.user("detail-a"), self.user("detail-b"), self.user("detail-x")
        match = self.make_match("detail", [player, opponent], datetime(2026, 11, 1, 12), with_progress=True)
        client = app.test_client()
        self.login(client, player)
        page = client.get(f"/pvp/history/{match.id}")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("History Player detail-b", body)
        self.assertIn("Невикористані спроби", body)
        self.assertIn("Мій прогрес за словами", body)
        self.assertIn("attempt-cell green", body)
        self.assertNotIn(player.email, body)
        self.assertNotIn(player.google_id, body)

        outsider_client = app.test_client()
        self.login(outsider_client, outsider)
        self.assertEqual(outsider_client.get(f"/pvp/history/{match.id}").status_code, 404)

    def test_active_match_window_expires_exactly_at_five_minutes_and_history_remains(self):
        player, opponent = self.user("window-a"), self.user("window-b")
        completed_at = datetime(2026, 11, 2, 10, 0, 0)
        match = self.make_match("window", [player, opponent], completed_at, with_memberships=True)
        old_lobby_id = match.lobby.public_id
        client = app.test_client()
        self.login(client, player)

        with patch("pvp.routes._utcnow", return_value=completed_at + timedelta(seconds=299)):
            self.assertEqual(client.get(f"/pvp/match/{match.public_id}").status_code, 200)
            self.assertEqual(client.get(f"/pvp/match/{match.public_id}/state").status_code, 200)
        with patch("pvp.routes._utcnow", return_value=completed_at + timedelta(seconds=300)):
            self.assertEqual(client.get(f"/pvp/match/{match.public_id}").status_code, 302)
            self.assertEqual(client.get(f"/pvp/match/{match.public_id}/state").status_code, 410)
            self.assertEqual(client.get(f"/pvp/{old_lobby_id}/state").status_code, 410)
            lobby_home = client.get("/pvp/").get_data(as_text=True)
            self.assertIn("Створити лобі", lobby_home)
            with client.session_transaction() as saved:
                csrf = saved["pvp_csrf_token"]
            closed = client.post(
                f"/pvp/match/{match.public_id}/state",
                data={"csrf_token": csrf},
            )
            self.assertEqual(closed.status_code, 410)
            self.assertEqual(client.get(f"/pvp/history/{match.id}").status_code, 200)

        with patch("pvp.routes._utcnow", return_value=completed_at + timedelta(minutes=10)):
            self.assertEqual(client.get(f"/pvp/match/{match.public_id}").status_code, 302)

        self.assertEqual(PvPLobbyMember.query.filter_by(user_id=player.id).count(), 0)
        self.assertEqual(PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=player.id).count(), 1)
        self.assertEqual(PvPMatchResult.query.filter_by(match_id=match.id).count(), 2)
        created = client.post("/pvp/create", data={"csrf_token": csrf})
        self.assertEqual(created.status_code, 302)
        self.assertEqual(PvPLobbyMember.query.filter_by(user_id=player.id).count(), 1)

    def test_legacy_completed_match_without_timestamp_is_closed_but_history_is_preserved(self):
        player = self.user("legacy")
        match = self.make_match("legacy-match", [player], datetime(2026, 10, 1), with_memberships=True)
        match.completed_at = None
        db.session.commit()
        client = app.test_client()
        self.login(client, player)
        self.assertEqual(client.get("/pvp/").status_code, 200)
        body = client.get("/pvp/").get_data(as_text=True)
        self.assertIn("Створити лобі", body)
        self.assertEqual(client.get(f"/pvp/match/{match.public_id}").status_code, 302)
        self.assertEqual(client.get(f"/pvp/history/{match.id}").status_code, 200)
        with client.session_transaction() as saved:
            csrf = saved["pvp_csrf_token"]
        client.post("/pvp/list-state", data={"csrf_token": csrf})
        self.assertEqual(PvPLobbyMember.query.filter_by(user_id=player.id).count(), 0)
        self.assertEqual(PvPMatchResult.query.filter_by(match_id=match.id).count(), 1)

    def test_left_participant_can_view_completed_results_during_grace_window(self):
        player, opponent = self.user("left-window-a"), self.user("left-window-b")
        completed_at = datetime(2026, 11, 2, 11)
        match = self.make_match("left-window", [player, opponent], completed_at)
        match_player = PvPMatchPlayer.query.filter_by(match_id=match.id, user_id=player.id).one()
        match_player.status = "left"
        match_player.score_frozen_at = completed_at
        db.session.commit()
        client = app.test_client()
        self.login(client, player)
        client.get("/pvp/")
        with client.session_transaction() as saved:
            csrf = saved["pvp_csrf_token"]

        with patch("pvp.routes._utcnow", return_value=completed_at + timedelta(seconds=299)):
            self.assertEqual(client.get(f"/pvp/match/{match.public_id}").status_code, 200)
            state = client.post(
                f"/pvp/match/{match.public_id}/state", data={"csrf_token": csrf}
            )
            self.assertEqual(state.status_code, 200)
            self.assertEqual(state.get_json()["status"], "completed")
            self.assertEqual(len(state.get_json()["results"]), 2)

        with patch("pvp.routes._utcnow", return_value=completed_at + timedelta(seconds=300)):
            closed = client.post(
                f"/pvp/match/{match.public_id}/state", data={"csrf_token": csrf}
            )
            self.assertEqual(closed.status_code, 410)
            self.assertEqual(client.get(f"/pvp/history/{match.id}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
