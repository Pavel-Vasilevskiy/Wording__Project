import unittest
from pathlib import Path

from app_with_auth import app
from models import GameSession, User, db


class GameStateRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".leaderboard-test.sqlite"
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

    def setUp(self):
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        self.app_context.pop()

    def create_client(self, target_word="ARISE"):
        user = User(
            google_id="restore-test",
            email="restore-test@example.test",
            name="Restore Tester",
        )
        db.session.add(user)
        db.session.commit()
        client = app.test_client()
        with client.session_transaction() as saved_session:
            saved_session["_user_id"] = str(user.id)
            saved_session["_fresh"] = True
            saved_session["target_word"] = target_word
            saved_session["tries"] = 0
            saved_session["guesses"] = []
            saved_session["game_over"] = False
            saved_session["won"] = False
        return client, user

    def test_three_guesses_restore_rows_keyboard_state_and_fourth_attempt(self):
        client, _ = self.create_client()
        submitted_results = []
        for word in ("crane", "stare", "adieu"):
            response = client.post("/guess", json={"guess": word})
            self.assertEqual(response.status_code, 200)
            submitted_results.append(response.get_json()["result"])

        with client.session_transaction() as before_reload:
            answer_before_reload = before_reload["target_word"]

        state = client.get("/game_state").get_json()
        repeated_state = client.get("/game_state").get_json()
        self.assertEqual(state["tries"], 3)
        self.assertEqual(state["guesses"], submitted_results)
        self.assertIsNone(state["target_word"])
        self.assertEqual(repeated_state, state)
        with client.session_transaction() as after_reload:
            self.assertEqual(after_reload["target_word"], answer_before_reload)

        # History result statuses are the same values rendered in the rows and
        # passed through the shared keyboard-priority updater in the frontend.
        self.assertEqual(state["guesses"][0][1]["status"], "green")
        self.assertEqual(state["guesses"][0][2]["status"], "yellow")
        self.assertEqual(state["guesses"][0][0]["status"], "gray")

        fourth = client.post("/guess", json={"guess": "point"})
        self.assertEqual(fourth.status_code, 200)
        self.assertEqual(fourth.get_json()["tries"], 4)
        with client.session_transaction() as after_fourth:
            self.assertEqual(after_fourth["target_word"], answer_before_reload)
            self.assertEqual(len(after_fourth["guesses"]), 4)

    def test_won_game_state_stays_completed_and_answer_is_revealed(self):
        client, user = self.create_client("CRANE")
        response = client.post("/guess", json={"guess": "crane"})
        self.assertTrue(response.get_json()["won"])

        state = client.get("/game_state").get_json()
        self.assertTrue(state["game_over"])
        self.assertTrue(state["won"])
        self.assertEqual(state["target_word"], "CRANE")
        self.assertEqual(state["tries"], 1)
        self.assertEqual(client.post("/guess", json={"guess": "arise"}).status_code, 409)
        self.assertEqual(GameSession.query.filter_by(user_id=user.id).count(), 1)

    def test_lost_game_state_stays_completed(self):
        client, user = self.create_client("ARISE")
        for _ in range(6):
            response = client.post("/guess", json={"guess": "crane"})
            self.assertEqual(response.status_code, 200)

        state = client.get("/game_state").get_json()
        self.assertTrue(state["game_over"])
        self.assertFalse(state["won"])
        self.assertEqual(state["target_word"], "ARISE")
        self.assertEqual(state["tries"], 6)
        self.assertEqual(client.post("/guess", json={"guess": "stare"}).status_code, 409)
        self.assertEqual(GameSession.query.filter_by(user_id=user.id).count(), 1)

    def test_invalid_guess_is_not_part_of_restored_history_and_new_game_is_empty(self):
        client, _ = self.create_client()
        invalid = client.post("/guess", json={"guess": "qzxjk"})
        self.assertEqual(invalid.status_code, 400)
        state = client.get("/game_state").get_json()
        self.assertEqual(state["tries"], 0)
        self.assertEqual(state["guesses"], [])

        client.post("/guess", json={"guess": "crane"})
        self.assertEqual(client.post("/new_game").status_code, 200)
        state = client.get("/game_state").get_json()
        self.assertEqual(state["tries"], 0)
        self.assertEqual(state["guesses"], [])
        self.assertFalse(state["game_over"])
        self.assertFalse(state["won"])
        self.assertIsNone(state["target_word"])

    def test_frontend_restores_rows_and_keyboard_before_enabling_submissions(self):
        script = (Path(__file__).resolve().parents[1] / "static" / "script.js").read_text(encoding="utf-8")
        self.assertIn("fetch('/game_state')", script)
        self.assertIn("state.guesses.forEach((result, rowIndex)", script)
        self.assertIn("updateGameBoard(result, rowIndex)", script)
        self.assertIn("updateKeyboardState(result)", script)
        self.assertIn("gameReady = true", script)
        self.assertIn("if (!gameReady || submissionPending) return", script)
        self.assertIn("Could not load your game. Please reload the page.", script)


if __name__ == "__main__":
    unittest.main()
