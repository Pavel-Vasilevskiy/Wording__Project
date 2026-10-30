import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.exc import OperationalError
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from app_with_auth import app, initialize_database
from models import db, GameSession, User


class LeaderboardPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".leaderboard-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="test-only-secret",
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
        self.app_context = app.app_context()
        self.app_context.push()
        with app.app_context():
            db.drop_all()
            db.create_all()

    def tearDown(self):
        db.session.remove()
        self.app_context.pop()

    def create_user(self, google_id, name, **stats):
        user = User(
            google_id=google_id,
            email=f"{google_id}@example.test",
            name=name,
            **stats,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, client, user):
        with client.session_transaction() as flask_session:
            flask_session["_user_id"] = str(user.id)
            flask_session["_fresh"] = True
            flask_session["target_word"] = "ARISE"
            flask_session["tries"] = 0
            flask_session["guesses"] = []
            flask_session["game_over"] = False
            flask_session["won"] = False

    def finish_game(self, user):
        client = app.test_client()
        self.login(client, user)
        return client.post("/guess", json={"guess": "ARISE"})

    def test_completed_game_and_personal_stats_are_saved_atomically(self):
        user = self.create_user("google-1", "Player One")

        response = self.finish_game(user)

        self.assertEqual(response.status_code, 200)
        with app.app_context():
            saved_user = db.session.get(User, user.id)
            game = GameSession.query.filter_by(user_id=user.id).one()
            self.assertTrue(game.won)
            self.assertEqual(game.tries_used, 1)
            self.assertEqual(json.loads(game.guesses)[0][0]["status"], "green")
            self.assertEqual(saved_user.games_played, 1)
            self.assertEqual(saved_user.games_won, 1)

    def test_leaderboard_reads_saved_user_results_and_keeps_existing_sort_order(self):
        low_rate = self.create_user(
            "google-low", "Low Rate", games_played=10, games_won=5,
            current_streak=1, max_streak=2,
        )
        high_rate_low_streak = self.create_user(
            "google-mid", "High Rate Low Streak", games_played=5, games_won=3,
            current_streak=1, max_streak=2,
        )
        high_rate_high_streak = self.create_user(
            "google-high", "High Rate High Streak", games_played=10, games_won=6,
            current_streak=2, max_streak=4,
        )

        client = app.test_client()
        self.login(client, low_rate)
        response = client.get("/leaderboard")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertLess(page.index("High Rate High Streak"), page.index("High Rate Low Streak"))
        self.assertLess(page.index("High Rate Low Streak"), page.index("Low Rate"))

    def test_result_survives_database_connection_reinitialization_and_new_process(self):
        user = self.create_user("google-restart", "Persistent Player")
        response = self.finish_game(user)
        self.assertEqual(response.status_code, 200)

        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            self.assertEqual(GameSession.query.filter_by(user_id=user.id).count(), 1)
            database_url = db.engine.url.render_as_string(hide_password=False)

        child_env = os.environ.copy()
        child_env["DATABASE_URL"] = database_url
        child_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        # User IDs are generated from the isolated test database; query by the stable Google ID.
        script = (
            "from app_with_auth import app; from models import db, User, GameSession; "
            "app.app_context().push(); "
            "u=User.query.filter_by(google_id='google-restart').one(); "
            "print(u.games_played, GameSession.query.filter_by(user_id=u.id).count())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 1", result.stdout)

    def test_completed_game_does_not_create_duplicate_result(self):
        user = self.create_user("google-duplicate", "One Result")
        client = app.test_client()
        self.login(client, user)

        self.assertEqual(client.post("/guess", json={"guess": "ARISE"}).status_code, 200)
        self.assertEqual(client.post("/guess", json={"guess": "ARISE"}).status_code, 409)
        with app.app_context():
            self.assertEqual(GameSession.query.filter_by(user_id=user.id).count(), 1)

    def test_database_write_failure_rolls_back_result_and_stats(self):
        user = self.create_user("google-failure", "Unchanged Player")
        client = app.test_client()
        self.login(client, user)

        with patch.object(
            db.session,
            "commit",
            side_effect=OperationalError("COMMIT", {}, Exception("database unavailable")),
        ):
            response = client.post("/guess", json={"guess": "ARISE"})

        self.assertEqual(response.status_code, 503)
        with app.app_context():
            saved_user = db.session.get(User, user.id)
            self.assertEqual(saved_user.games_played, 0)
            self.assertEqual(GameSession.query.filter_by(user_id=user.id).count(), 0)

    def test_database_read_failure_returns_temporary_unavailable_page(self):
        user = self.create_user("google-read-failure", "Reader")
        client = app.test_client()
        self.login(client, user)

        original_query = User.query

        class FailingLeaderboardQuery:
            def get(self, user_id):
                return original_query.get(user_id)

            def filter(self, *args, **kwargs):
                return self

            def all(self):
                raise OperationalError("SELECT", {}, Exception("database unavailable"))

        with patch.object(User, "query", new=FailingLeaderboardQuery()):
            response = client.get("/leaderboard")

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"temporarily unavailable", response.data)

    def test_startup_initializes_tables_in_an_empty_database(self):
        db.drop_all()

        initialize_database()

        self.assertEqual(
            set(inspect(db.engine).get_table_names()),
            {"user", "game_session", "pvp_lobby", "pvp_lobby_member", "pvp_lobby_kick_notice"},
        )

    def test_fly_config_uses_the_persistent_volume_path_in_fra(self):
        fly_config = tomllib.loads((Path(__file__).resolve().parents[1] / "fly.toml").read_text())

        self.assertEqual(fly_config["primary_region"], "fra")
        self.assertEqual(fly_config["mounts"]["source"], "wordle_data")
        self.assertEqual(fly_config["mounts"]["destination"], "/data")
        self.assertEqual(make_url(fly_config["env"]["DATABASE_URL"]).database, "/data/wordle_users.db")


if __name__ == "__main__":
    unittest.main()
