import io
import re
import sqlite3
import unittest
from pathlib import Path

from flask import g
from PIL import Image
from sqlalchemy import create_engine, inspect

from app_with_auth import app
from models import User, db
from pvp.schema import upgrade_pvp_schema


class PlayerAvatarUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = Path.cwd() / ".avatar_upload_test_data"
        cls.temp_root.mkdir(exist_ok=True)
        cls.database_path = cls.temp_root / "avatars.sqlite"
        app.config.update(
            TESTING=True,
            SECRET_KEY="avatar-test-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{cls.database_path}",
            AVATAR_UPLOAD_DIR=str(cls.temp_root / "stored-avatars"),
            AVATAR_MAX_BYTES=5 * 1024 * 1024,
            MAX_CONTENT_LENGTH=5 * 1024 * 1024 + 128 * 1024,
        )

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        for child in cls.temp_root.iterdir():
            if child.is_file():
                child.unlink()
            else:
                for item in child.iterdir():
                    item.unlink()
                child.rmdir()
        cls.temp_root.rmdir()

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        Path(app.config["AVATAR_UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
        self.user = User(
            google_id="avatar-google-id",
            email="avatar-private@example.test",
            name="Avatar Player",
            picture="https://google.example/avatar.jpg",
        )
        db.session.add(self.user)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def login(self):
        g.pop("_login_user", None)
        with self.client.session_transaction() as saved:
            saved["_user_id"] = str(self.user.id)
            saved["_fresh"] = True

    def csrf_token(self):
        page = self.client.get("/profile").get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]+)"', page)
        self.assertIsNotNone(match)
        return match.group(1)

    @staticmethod
    def image_bytes(image_format):
        output = io.BytesIO()
        Image.new("RGB", (80, 48), (40, 120, 200)).save(output, format=image_format)
        return output.getvalue()

    def upload(self, filename, image_format, mime=None, token=None, data=None):
        self.login()
        return self.client.post(
            "/profile/avatar",
            data={
                "csrf_token": token or self.csrf_token(),
                "avatar": (io.BytesIO(data if data is not None else self.image_bytes(image_format)), filename, mime or f"image/{image_format.lower()}"),
            },
            content_type="multipart/form-data",
        )

    def test_jpeg_png_and_webp_are_normalized_and_custom_precedes_google(self):
        for extension, fmt, mime in (
            ("photo.jpg", "JPEG", "image/jpeg"),
            ("photo.png", "PNG", "image/png"),
            ("photo.webp", "WEBP", "image/webp"),
        ):
            response = self.upload(extension, fmt, mime)
            self.assertEqual(response.status_code, 302)
            db.session.refresh(self.user)
            stored = Path(app.config["AVATAR_UPLOAD_DIR"]) / self.user.custom_avatar
            with Image.open(stored) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.size, (512, 512))
            profile = self.client.get("/profile").get_data(as_text=True)
            self.assertIn(f"/avatars/{self.user.custom_avatar}", profile)
            self.assertIn("https://google.example/avatar.jpg", self.user.picture)

    def test_unsupported_mime_extensions_corrupt_images_and_oversize_are_rejected(self):
        for filename, fmt, mime, raw in (
            ("vector.svg", "PNG", "image/svg+xml", self.image_bytes("PNG")),
            ("motion.gif", "GIF", "image/gif", self.image_bytes("GIF")),
            ("broken.png", "PNG", "image/png", b"not an image"),
        ):
            response = self.upload(filename, fmt, mime, data=raw)
            self.assertEqual(response.status_code, 302)
            db.session.refresh(self.user)
            self.assertIsNone(self.user.custom_avatar)

        response = self.upload("large.png", "PNG", "image/png", data=b"x" * (5 * 1024 * 1024 + 1))
        # Fetch the redirected profile so the flash message is included in the response.
        response = self.client.get(response.location)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"File is too large", response.data)
        self.assertIsNone(self.user.custom_avatar)

    def test_login_csrf_and_user_scope_are_enforced(self):
        response = self.client.post("/profile/avatar", data={})
        self.assertEqual(response.status_code, 302)
        self.login()
        response = self.client.post(
            "/profile/avatar",
            data={"csrf_token": "forged"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        other = User(
            google_id="other-avatar-google",
            email="other-avatar@example.test",
            name="Other",
        )
        db.session.add(other)
        db.session.commit()
        response = self.client.post(
            "/profile/avatar",
            data={
                "csrf_token": self.csrf_token(),
                "user_id": str(other.id),
                "avatar": (io.BytesIO(self.image_bytes("PNG")), "mine.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.user)
        db.session.refresh(other)
        self.assertIsNotNone(self.user.custom_avatar)
        self.assertIsNone(other.custom_avatar)

    def test_replacement_serving_and_delete_preserve_google_picture(self):
        self.login()
        self.upload("first.png", "PNG", "image/png")
        first_name = self.user.custom_avatar
        first_path = Path(app.config["AVATAR_UPLOAD_DIR"]) / first_name
        self.assertTrue(first_path.exists())
        self.upload("second.png", "PNG", "image/png")
        second_name = self.user.custom_avatar
        self.assertNotEqual(first_name, second_name)
        self.assertFalse(first_path.exists())
        served = self.client.get(f"/avatars/{second_name}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.mimetype, "image/webp")
        self.assertIn("immutable", served.headers["Cache-Control"])
        served.close()
        self.assertEqual(self.client.get("/avatars/../../secret").status_code, 404)

        token = self.csrf_token()
        response = self.client.post("/profile/avatar/delete", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.user)
        self.assertIsNone(self.user.custom_avatar)
        self.assertEqual(self.user.picture, "https://google.example/avatar.jpg")
        self.assertFalse((Path(app.config["AVATAR_UPLOAD_DIR"]) / second_name).exists())
        self.assertIn("https://google.example/avatar.jpg", self.client.get("/profile").get_data(as_text=True))

    def test_user_without_google_or_custom_avatar_uses_initial_and_public_views_use_custom(self):
        self.login()
        self.user.picture = None
        db.session.commit()
        self.assertIn(b">A</span>", self.client.get("/profile").data)
        self.upload("avatar.png", "PNG", "image/png")
        custom_name = self.user.custom_avatar
        from pvp.models import PvPPlayerStats
        db.session.add(PvPPlayerStats(
            user_id=self.user.id, matches_played=1, wins=0, total_score=1,
            words_solved=0, total_attempts=0, solved_words_count=0,
            best_match_score=1,
        ))
        db.session.commit()
        body = self.client.get("/pvp/leaderboard").get_data(as_text=True)
        self.assertIn(f"/avatars/{custom_name}", body)
        self.assertNotIn("avatar-private@example.test", body)

    def test_existing_user_table_upgrade_is_additive(self):
        legacy_path = self.temp_root / "legacy.sqlite"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            "CREATE TABLE user (id INTEGER PRIMARY KEY, google_id VARCHAR(100), "
            "email VARCHAR(100), name VARCHAR(100), picture VARCHAR(200))"
        )
        connection.execute(
            "INSERT INTO user (id, google_id, email, name, picture) "
            "VALUES (1, 'legacy-google', 'legacy@example.test', 'Legacy', 'https://g.example/a')"
        )
        connection.commit()
        connection.close()
        engine = create_engine(f"sqlite:///{legacy_path}")
        upgrade_pvp_schema(engine)
        columns = {item["name"] for item in inspect(engine).get_columns("user")}
        self.assertIn("custom_avatar", columns)
        with engine.connect() as conn:
            legacy = conn.exec_driver_sql("SELECT name, picture, custom_avatar FROM user WHERE id = 1").one()
        self.assertEqual(tuple(legacy), ("Legacy", "https://g.example/a", None))
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
