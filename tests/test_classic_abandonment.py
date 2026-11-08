import json
import re
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from flask import g
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SQLAlchemyError

from app_with_auth import app
from models import GameSession, User, db
from pvp.schema import upgrade_pvp_schema


class ClassicAbandonedGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".classic-abandonment.sqlite"
        app.config.update(
            TESTING=True,
            SECRET_KEY="classic-abandonment-test-secret",
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
        self.user = User(
            google_id=f"classic-abandon-{uuid.uuid4().hex}",
            email=f"{uuid.uuid4().hex}@example.test",
            name="Classic Player",
            games_played=10,
            games_won=7,
            current_streak=2,
            max_streak=5,
        )
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def client_with_game(self, target="ARISE", guesses=None, *, game_over=False, won=False, game_id=None):
        client = app.test_client()
        with client.session_transaction() as saved:
            saved["_user_id"] = str(self.user.id)
            saved["_fresh"] = True
            saved["target_word"] = target
            saved["tries"] = len(guesses or [])
            saved["guesses"] = guesses or []
            saved["game_over"] = game_over
            saved["won"] = won
            saved["game_id"] = game_id or uuid.uuid4().hex
        g.pop("_login_user", None)
        return client

    @staticmethod
    def guess_result(word):
        return [{"letter": letter, "status": "gray"} for letter in word]

    def test_win_and_ordinary_loss_update_games_wins_streak_and_best(self):
        self.user.current_streak = 5
        self.user.max_streak = 5
        db.session.commit()
        winner = self.client_with_game(target="CRANE")
        response = winner.post("/guess", json={"guess": "crane"})
        self.assertEqual(response.status_code, 200)
        db.session.refresh(self.user)
        self.assertEqual((self.user.games_played, self.user.games_won), (11, 8))
        self.assertEqual((self.user.current_streak, self.user.max_streak), (6, 6))
        self.assertAlmostEqual(self.user.win_percentage, 72.7)

        loser = self.client_with_game(target="ARISE")
        for _ in range(6):
            response = loser.post("/guess", json={"guess": "crane"})
            self.assertEqual(response.status_code, 200)
        db.session.refresh(self.user)
        self.assertEqual((self.user.games_played, self.user.games_won), (12, 8))
        self.assertEqual((self.user.current_streak, self.user.max_streak), (0, 6))
        sessions = GameSession.query.filter_by(user_id=self.user.id).order_by(GameSession.id).all()
        self.assertEqual([row.won for row in sessions], [True, False])
        self.assertTrue(all(row.game_id for row in sessions))

    def test_restart_after_attempt_records_one_abandoned_loss_and_refreshes_stats(self):
        client = self.client_with_game()
        guess_response = client.post("/guess", json={"guess": "crane"})
        self.assertEqual(guess_response.status_code, 200)
        response = client.post("/new_game")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["abandoned_loss"])
        self.assertEqual(response.get_json()["user_stats"]["games_played"], 11)
        db.session.refresh(self.user)
        abandoned = GameSession.query.filter_by(user_id=self.user.id).one()
        self.assertFalse(abandoned.won)
        self.assertEqual(abandoned.tries_used, 1)
        self.assertEqual(json.loads(abandoned.guesses), [guess_response.get_json()["result"]])
        self.assertEqual((self.user.games_played, self.user.games_won), (11, 7))
        self.assertEqual((self.user.current_streak, self.user.max_streak), (0, 5))
        self.assertAlmostEqual(self.user.win_percentage, 63.6)
        self.assertEqual(client.get("/game_state").get_json()["tries"], 0)
        db.session.refresh(self.user)
        self.assertEqual((self.user.games_played, self.user.games_won), (11, 7))

    def test_zero_attempt_restart_does_not_change_statistics_or_create_history(self):
        client = self.client_with_game()
        response = client.post("/new_game")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["abandoned_loss"])
        self.assertEqual(GameSession.query.filter_by(user_id=self.user.id).count(), 0)
        db.session.refresh(self.user)
        self.assertEqual((self.user.games_played, self.user.games_won), (10, 7))
        self.assertEqual((self.user.current_streak, self.user.max_streak), (2, 5))

    def test_duplicate_stale_request_records_an_abandoned_game_only_once(self):
        game_id = uuid.uuid4().hex
        client = self.client_with_game(
            guesses=[self.guess_result("CRANE")], game_id=game_id
        )
        old_cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        retry_client = app.test_client()
        retry_client.set_cookie(app.config.get("SESSION_COOKIE_NAME", "session"), old_cookie.value)

        first = client.post("/new_game")
        retry = retry_client.post("/new_game")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(GameSession.query.filter_by(game_id=game_id).count(), 1)
        db.session.refresh(self.user)
        self.assertEqual((self.user.games_played, self.user.games_won), (11, 7))
        self.assertEqual((self.user.current_streak, self.user.max_streak), (0, 5))

    def test_terminal_win_and_loss_are_not_counted_again_by_new_game(self):
        for target, guess, expected_games in (("CRANE", "crane", 11), ("ARISE", "crane", 12)):
            client = self.client_with_game(target=target)
            limit = 1 if guess == target.lower() else 6
            for _ in range(limit):
                response = client.post("/guess", json={"guess": guess})
                self.assertEqual(response.status_code, 200)
            before = (self.user.games_played, self.user.games_won)
            restarted = client.post("/new_game")
            self.assertEqual(restarted.status_code, 200)
            self.assertFalse(restarted.get_json()["abandoned_loss"])
            db.session.refresh(self.user)
            self.assertEqual((self.user.games_played, self.user.games_won), before)
            self.assertEqual((self.user.games_played, self.user.games_won), (expected_games, 8))

    def test_database_failure_keeps_current_game_and_does_not_start_another(self):
        client = self.client_with_game(
            guesses=[self.guess_result("CRANE")]
        )
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("temporary failure")):
            response = client.post("/new_game")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Unable to record", response.get_json()["error"])
        state = client.get("/game_state").get_json()
        self.assertEqual(state["tries"], 1)
        self.assertEqual(len(state["guesses"]), 1)
        self.assertEqual(GameSession.query.filter_by(user_id=self.user.id).count(), 0)
        db.session.refresh(self.user)
        self.assertEqual(self.user.games_played, 10)

    def test_classic_sidebar_footer_and_profile_leaderboard_link(self):
        client = self.client_with_game()
        page = client.get("/wordle").get_data(as_text=True)
        sidebar = page.split('<div class="sidebar" id="sidebar">', 1)[1].split(
            '<div class="main-content">', 1
        )[0]
        self.assertEqual(
            re.findall(r'id="(games-count|win-rate|current-streak|max-streak)"', sidebar),
            ["games-count", "win-rate", "current-streak", "max-streak"],
        )
        self.assertNotIn("sidebar-avatar", sidebar)
        self.assertNotIn("sidebar-username", sidebar)
        self.assertNotIn("sidebar-actions", page)
        self.assertNotIn("Wordle by Pavlo", page)
        styles = (Path(__file__).resolve().parents[1] / "static" / "css" / "classic-wordle.css").read_text(encoding="utf-8")
        self.assertIn("--classic-sidebar-offset: 90px", styles)
        self.assertIn("bottom: 0", styles)
        self.assertIn("z-index: 20", styles)

        profile = client.get("/profile").get_data(as_text=True)
        self.assertIn('href="/leaderboard"', profile)
        self.assertIn("Classic Leaderboard", profile)

    def test_game_id_upgrade_preserves_existing_classic_history(self):
        legacy_path = Path.cwd() / f".classic-legacy-{uuid.uuid4().hex}.sqlite"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            "CREATE TABLE game_session (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "target_word VARCHAR(5) NOT NULL, guesses TEXT, won BOOLEAN, tries_used INTEGER, "
            "completed_at DATETIME, created_at DATETIME)"
        )
        connection.execute(
            "INSERT INTO game_session (id, user_id, target_word, guesses, won, tries_used) "
            "VALUES (1, 4, 'CRANE', '[]', 1, 2)"
        )
        connection.commit()
        connection.close()
        engine = create_engine(f"sqlite:///{legacy_path}")
        try:
            upgrade_pvp_schema(engine)
            columns = {column["name"] for column in inspect(engine).get_columns("game_session")}
            indexes = inspect(engine).get_indexes("game_session")
            self.assertIn("game_id", columns)
            self.assertTrue(any(index["name"] == "uq_game_session_game_id" and index["unique"] for index in indexes))
            with engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT user_id, target_word, won, tries_used, game_id FROM game_session WHERE id=1"
                ).one()
            self.assertEqual(tuple(row), (4, "CRANE", 1, 2, None))
        finally:
            engine.dispose()
            legacy_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
