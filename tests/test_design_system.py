import unittest
from pathlib import Path

from flask import g

from app_with_auth import app
from models import User, db


class GlobalDesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".design-system-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="design-system-test-secret",
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

    def login(self, client, user):
        g.pop("_login_user", None)
        with client.session_transaction() as saved:
            saved["_user_id"] = str(user.id)
            saved["_fresh"] = True

    def test_authenticated_navigation_and_public_portal_state(self):
        client = app.test_client()
        public_home = client.get("/").get_data(as_text=True)
        self.assertIn('class="portal-site-login"', public_home)
        self.assertNotIn('class="portal-site-links"', public_home)

        user = User(
            google_id="design-system-player", email="private@design.test",
            name="Design Player", picture="https://example.test/design-player.png",
        )
        db.session.add(user)
        db.session.commit()
        self.login(client, user)
        page = client.get("/wordle").get_data(as_text=True)
        for href in ('/wordle', '/pvp/', '/pvp/leaderboard', '/pvp/history', '/profile'):
            self.assertIn(f'href="{href}"', page)
        self.assertIn('class="portal-site-avatar-link"', page)
        self.assertIn('aria-current="page"', page)
        self.assertIn("design-system.css", page)

        profile = client.get("/profile").get_data(as_text=True)
        self.assertIn("dashboard-decoration--top-right", profile)
        self.assertIn('class="avatar-fallback"', profile)
        self.assertIn("this.hidden=true;this.nextElementSibling.hidden=false", profile)

    def test_core_pages_use_shared_navigation_and_system_stylesheet(self):
        templates_root = Path(__file__).resolve().parents[1] / "templates"
        expected = {
            "index.html", "pvp_lobbies.html", "pvp_lobby_detail.html", "pvp_match.html",
            "pvp_leaderboard.html", "pvp_history.html", "pvp_history_detail.html", "profile.html",
        }
        for name in expected:
            with self.subTest(template=name):
                body = (templates_root / name).read_text(encoding="utf-8")
                self.assertIn("macros/site_navigation.html", body)
                self.assertIn("css/design-system.css", body)
        lobby_detail = (templates_root / "pvp_lobby_detail.html").read_text(encoding="utf-8")
        self.assertIn("show_profile=false", lobby_detail)

        stylesheet = (templates_root.parent / "static" / "css" / "design-system.css").read_text(encoding="utf-8")
        for token in ("--bg-primary", "--accent-primary", "--card-background", "--button-background", "--input-background"):
            self.assertIn(token, stylesheet)
        self.assertIn("pointer-events: none", stylesheet)

    def test_navigation_uses_one_flow_shell_and_accessible_mobile_menu(self):
        macro = (Path(__file__).resolve().parents[1] / "templates" / "macros" / "site_navigation.html").read_text(encoding="utf-8")
        stylesheet = (Path(__file__).resolve().parents[1] / "static" / "css" / "design-system.css").read_text(encoding="utf-8")
        navigation_script = (Path(__file__).resolve().parents[1] / "static" / "site_navigation.js").read_text(encoding="utf-8")
        self.assertIn("portal-header-shell", macro)
        self.assertIn('aria-expanded="false"', macro)
        self.assertIn("aria-controls=", macro)
        self.assertIn("data-site-menu-toggle", macro)
        self.assertIn("data-open=\"true\"", stylesheet)
        self.assertIn("max-width: 700px", stylesheet)
        self.assertIn('event.key === "Escape"', navigation_script)
        self.assertIn("!root.contains(event.target)", navigation_script)

    def test_classic_wordle_has_separate_profile_aligned_styles(self):
        project_root = Path(__file__).resolve().parents[1]
        template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
        stylesheet = (project_root / "static" / "css" / "classic-wordle.css").read_text(encoding="utf-8")
        self.assertIn("css/classic-wordle.css", template)
        self.assertIn("classic-game-heading", template)
        for selector in (".letter-box.green", ".letter-box.yellow", ".letter-box.gray", ".key.correct", ".key.present", ".key.absent"):
            self.assertIn(selector, stylesheet)
        self.assertIn("max-width: 360px", stylesheet)


if __name__ == "__main__":
    unittest.main()
