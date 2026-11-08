import unittest
from datetime import datetime, timedelta
from pathlib import Path

from flask import g

from app_with_auth import app
from models import User, db
from pvp.models import PvPLobby, PvPMatch, PvPMatchPlayer, PvPMatchResult, PvPPlayerStats


class PlayerProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".player-profile-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="player-profile-test-secret",
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

    def user(self, key, **kwargs):
        user = User(
            google_id=f"profile-google-{key}",
            email=f"private-{key}@example.test",
            name=kwargs.pop("name", f"Player {key}"),
            **kwargs,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, client, user):
        g.pop("_login_user", None)
        with client.session_transaction() as saved:
            saved["_user_id"] = str(user.id)
            saved["_fresh"] = True

    def test_dashboard_uses_pvp_stats_and_private_history_without_email(self):
        user = self.user("owner", name="A Very Long Player Nickname")
        db.session.add(PvPPlayerStats(
            user_id=user.id, matches_played=2, wins=1, total_score=900,
            words_solved=3, total_attempts=9, solved_words_count=3,
            best_match_score=600,
        ))
        lobby = PvPLobby(leader_id=user.id)
        db.session.add(lobby)
        db.session.flush()
        now = datetime.utcnow()
        match = PvPMatch(
            lobby_id=lobby.id, status="completed", game_mode="timer", words_count=5,
            timer_seconds=120, attempts_per_word=6, current_round=5,
            countdown_started_at=now - timedelta(minutes=2),
            scheduled_start_at=now - timedelta(minutes=2), started_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
        )
        db.session.add(match)
        db.session.flush()
        db.session.add(PvPMatchPlayer(match_id=match.id, user_id=user.id, status="active"))
        db.session.add(PvPMatchResult(
            match_id=match.id, user_id=user.id, game_mode="timer", words_count=5,
            final_score=500, place=1, is_winner=True, words_solved=2,
            total_attempts=6, completed_words=5, participation_status="completed",
            score_breakdown="{}", finalized_at=now - timedelta(minutes=1),
        ))
        db.session.commit()

        client = app.test_client()
        self.login(client, user)
        response = client.get("/profile")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Global Rank", page)
        self.assertIn(">#1</strong>", page)
        self.assertIn("50.0%", page)
        self.assertIn("3.00", page)
        self.assertIn("Recent Matches", page)
        self.assertIn(f"/pvp/history/{match.id}", page)
        self.assertIn("Account Settings", page)
        self.assertIn('href="/leaderboard"', page)
        self.assertIn("Classic Leaderboard", page)
        self.assertNotIn(user.email, page)
        self.assertNotIn(user.google_id, page)
        self.assertIn('href="/logout"', page)

    def test_authentication_public_profile_privacy_and_missing_user(self):
        owner = self.user("owner", picture=None)
        other = self.user("other", name="Public Player")
        db.session.add(PvPPlayerStats(
            user_id=other.id, matches_played=1, wins=0, total_score=20,
            words_solved=0, total_attempts=0, solved_words_count=0,
            best_match_score=20,
        ))
        db.session.commit()

        client = app.test_client()
        self.assertEqual(client.get("/profile").status_code, 302)
        response = client.get(f"/profile/{other.id}")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Public Player", page)
        self.assertIn("Global Rank", page)
        self.assertIn(">#1</strong>", page)
        self.assertIn(">P</span>", page)  # Initial fallback avatar.
        self.assertNotIn(other.email, page)
        self.assertNotIn(other.google_id, page)
        self.assertNotIn("Account Settings", page)
        self.assertNotIn('href="/logout"', page)
        self.assertEqual(client.get("/profile/999999").status_code, 404)

        self.login(client, owner)
        self.assertEqual(client.get("/profile").status_code, 200)
        self.assertEqual(client.get(f"/profile/{owner.id}").status_code, 200)

    def test_empty_profile_and_unranked_placeholder(self):
        user = self.user("empty")
        client = app.test_client()
        self.login(client, user)
        response = client.get("/profile")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Unranked", page)
        self.assertIn("No completed PvP matches yet", page)
        self.assertIn("0%", page)
        self.assertIn("Average Attempts", page)
        self.assertIn("—", page)


if __name__ == "__main__":
    unittest.main()
