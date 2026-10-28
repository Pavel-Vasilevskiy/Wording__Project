import unittest
from pathlib import Path

from app_with_auth import app
from models import User, db


class PortalHomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".portal-home-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="portal-test-secret",
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

    def test_homepage_renders_wordle_mode_selector_and_portal_catalog(self):
        app.config["PORTAL_NAME"] = "Тестовий портал"
        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Тестовий портал", page)
        self.assertIn('<a class="game-link" href="/wordle">Classic Wordle</a>', page)
        self.assertIn('aria-label="Режими Wordle"', page)
        self.assertIn('class="game-mode-unavailable" type="button" disabled', page)
        self.assertIn("PvP Multiplayer", page)
        self.assertIn("Скоро", page)
        self.assertNotIn('href="/pvp"', page)
        self.assertIn("Судоку", page)
        self.assertIn("Memory", page)
        self.assertEqual(page.count('class="game-link"'), 1)
        self.assertIn('class="coming-soon"', page)
        self.assertEqual(page.count('class="game-card"'), 3)
        self.assertEqual(app.test_client().get("/pvp").status_code, 404)

    def test_homepage_is_public_and_wordle_keeps_login_requirement(self):
        client = app.test_client()

        self.assertEqual(client.get("/").status_code, 200)
        game = client.get("/wordle")
        self.assertEqual(game.status_code, 302)
        self.assertTrue(game.headers["Location"].endswith("/login?next=%2Fwordle"))

    def test_authenticated_game_route_preserves_existing_game_and_api_routes(self):
        user = User(google_id="portal-test-user", email="portal@example.test", name="Portal Tester")
        db.session.add(user)
        db.session.commit()
        client = app.test_client()
        with client.session_transaction() as saved_session:
            saved_session["_user_id"] = str(user.id)
            saved_session["_fresh"] = True
            saved_session["target_word"] = "ARISE"
            saved_session["tries"] = 0
            saved_session["guesses"] = []
            saved_session["game_over"] = False
            saved_session["won"] = False

        self.assertEqual(client.get("/wordle").status_code, 200)
        self.assertEqual(client.get("/game_state").status_code, 200)
        self.assertEqual(client.get("/profile").status_code, 200)
        self.assertEqual(client.get("/leaderboard").status_code, 200)
        self.assertEqual(client.post("/new_game").status_code, 200)
        self.assertEqual(client.get("/show_word").status_code, 404)

    def test_wordle_page_links_to_portal_without_logging_out_or_resetting_game(self):
        user = User(google_id="navigation-test-user", email="navigation@example.test", name="Navigation Tester")
        db.session.add(user)
        db.session.commit()
        client = app.test_client()
        previous_guesses = [[
            {"letter": "C", "status": "gray"},
            {"letter": "R", "status": "green"},
            {"letter": "A", "status": "yellow"},
            {"letter": "N", "status": "gray"},
            {"letter": "E", "status": "green"},
        ]]
        with client.session_transaction() as saved_session:
            saved_session["_user_id"] = str(user.id)
            saved_session["_fresh"] = True
            saved_session["target_word"] = "ARISE"
            saved_session["tries"] = 1
            saved_session["guesses"] = previous_guesses
            saved_session["game_over"] = False
            saved_session["won"] = False

        game = client.get("/wordle")
        self.assertEqual(game.status_code, 200)
        page = game.get_data(as_text=True)
        sidebar_actions = page.split('<div class="sidebar-actions">', 1)[1].split('</div>', 1)[0]
        self.assertEqual(page.count('id="new-game"'), 1)
        self.assertIn('id="new-game"', sidebar_actions)
        self.assertNotIn('<div class="controls">', page)
        self.assertNotIn('id="show-word"', page)
        self.assertNotIn("Show Word", page)
        self.assertIn('href="/" class="portal-game-home"', page)
        self.assertIn("← До всіх ігор", page)
        self.assertNotIn('id="guess-input"', page)
        self.assertNotIn('id="submit-guess"', page)
        self.assertNotIn('class="portal-game-home" href="/logout"', page)

        before_navigation = client.get("/game_state").get_json()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/wordle").status_code, 200)
        after_navigation = client.get("/game_state").get_json()
        self.assertEqual(after_navigation, before_navigation)
        self.assertEqual(after_navigation["tries"], 1)
        self.assertEqual(after_navigation["guesses"], previous_guesses)


if __name__ == "__main__":
    unittest.main()
