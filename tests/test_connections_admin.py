import re
import unittest
from pathlib import Path

from flask import g
from sqlalchemy import create_engine, inspect, text

from app_with_auth import app
from connections.models import ConnectionsCategory, ConnectionsPuzzle, ConnectionsWord
from models import User, db
from pvp.schema import upgrade_pvp_schema


class ConnectionsAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".connections-admin-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="connections-admin-test-secret",
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
        self.admin = self.create_user("admin", is_admin=True)
        self.member = self.create_user("member")
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def create_user(self, suffix, is_admin=False):
        user = User(
            google_id=f"connections-{suffix}",
            email=f"{suffix}@connections.test",
            name=f"{suffix.title()} Player",
            is_admin=is_admin,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, user):
        g.pop("_login_user", None)
        with self.client.session_transaction() as saved:
            saved["_user_id"] = str(user.id)
            saved["_fresh"] = True
        g.pop("_login_user", None)

    def csrf_token(self):
        g.pop("_login_user", None)
        response = self.client.get("/admin/connections/puzzles")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def categories(self, duplicate=False):
        values = []
        for category_index, difficulty in enumerate(("yellow", "green", "blue", "purple")):
            words = [f"word-{category_index}-{index}" for index in range(4)]
            values.append({
                "name": f"Group {category_index + 1}",
                "difficulty": difficulty,
                "description": "Optional explanation",
                "words": words,
            })
        if duplicate:
            values[1]["words"][0] = values[0]["words"][0].upper()
        return values

    def payload(self, status="draft", **overrides):
        payload = {"title": "Animal connections", "language": "en", "status": status, "categories": self.categories()}
        payload.update(overrides)
        return payload

    def api(self, method, path, payload=None, token=None):
        g.pop("_login_user", None)
        return self.client.open(
            path,
            method=method,
            json=payload,
            headers={"X-CSRF-Token": token} if token else {},
        )

    def test_admin_html_and_api_access_control(self):
        self.assertEqual(self.client.get("/admin/connections/puzzles").status_code, 302)
        self.assertEqual(self.client.get("/admin/connections/api/puzzles").status_code, 302)
        self.login(self.member)
        self.assertEqual(self.client.get("/admin/connections/puzzles").status_code, 403)
        self.assertEqual(self.client.get("/admin/connections/api/puzzles").status_code, 403)
        self.assertEqual(self.api("POST", "/admin/connections/api/puzzles", self.payload()).status_code, 403)
        self.login(self.admin)
        response = self.client.get("/admin/connections/puzzles")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Puzzle Library", response.data)
        self.assertIn(b"Connections Admin", response.data)

    def test_draft_create_relationships_edit_and_status_change(self):
        self.login(self.admin)
        token = self.csrf_token()
        draft_payload = self.payload(categories=[{"name": "Pets", "difficulty": "yellow", "words": ["dog"]}])
        created = self.api("POST", "/admin/connections/api/puzzles", draft_payload, token)
        self.assertEqual(created.status_code, 201, created.get_json())
        puzzle_id = created.get_json()["puzzle"]["id"]
        puzzle = db.session.get(ConnectionsPuzzle, puzzle_id)
        self.assertEqual(puzzle.created_by, self.admin.id)
        self.assertEqual(len(puzzle.categories), 4)
        self.assertEqual([word.text for word in puzzle.categories[0].words], ["dog", "", "", ""])
        self.assertEqual(sum(len(category.words) for category in puzzle.categories), 16)

        updated_payload = self.payload(status="approved", title="Updated puzzle")
        for index, category in enumerate(puzzle.categories):
            updated_payload["categories"][index]["id"] = category.id
        updated = self.api("PUT", f"/admin/connections/api/puzzles/{puzzle_id}", updated_payload, token)
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(puzzle.title, "Updated puzzle")
        self.assertEqual(puzzle.status, "approved")
        self.assertEqual(ConnectionsCategory.query.filter_by(puzzle_id=puzzle_id).count(), 4)
        self.assertEqual(ConnectionsWord.query.join(ConnectionsCategory).filter_by(puzzle_id=puzzle_id).count(), 16)

    def test_validation_rejects_status_difficulty_duplicates_and_foreign_category_ids(self):
        self.login(self.admin)
        token = self.csrf_token()
        cases = [
            (self.payload(status="published"), "status"),
            (self.payload(categories=[{"name": "x", "difficulty": "red", "words": []}]), "difficulty"),
            (self.payload(status="approved", categories=self.categories(duplicate=True)), "unique"),
        ]
        for payload, fragment in cases:
            response = self.api("POST", "/admin/connections/api/puzzles", payload, token)
            self.assertEqual(response.status_code, 400)
            self.assertIn(fragment, response.get_json()["error"].lower())

        created = self.api("POST", "/admin/connections/api/puzzles", self.payload(), token)
        puzzle_id = created.get_json()["puzzle"]["id"]
        forged = self.payload()
        forged["categories"][0]["id"] = 99999
        response = self.api("PUT", f"/admin/connections/api/puzzles/{puzzle_id}", forged, token)
        self.assertEqual(response.status_code, 400)
        self.assertIn("belong", response.get_json()["error"].lower())

    def test_library_search_filter_sort_and_pagination(self):
        self.login(self.admin)
        token = self.csrf_token()
        for index in range(23):
            payload = self.payload(title=f"Puzzle {index:02d}", status="approved")
            self.assertEqual(self.api("POST", "/admin/connections/api/puzzles", payload, token).status_code, 201)
        page = self.client.get("/admin/connections/puzzles?page=2&status=approved&sort=title&direction=asc&q=Puzzle")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Puzzle 20", html)
        self.assertIn("Puzzle 22", html)
        self.assertNotIn("Puzzle 19", html)
        self.assertIn("page=1", html)
        invalid_page = self.client.get("/admin/connections/puzzles?page=-9")
        self.assertEqual(invalid_page.status_code, 200)
        self.assertIn("Page 1 of 2", invalid_page.get_data(as_text=True))

    def test_archive_and_safe_delete_of_unused_puzzle(self):
        self.login(self.admin)
        token = self.csrf_token()
        created = self.api("POST", "/admin/connections/api/puzzles", self.payload(), token)
        puzzle_id = created.get_json()["puzzle"]["id"]
        archived = self.client.post(
            f"/admin/connections/puzzles/{puzzle_id}/archive",
            data={"csrf_token": token},
        )
        self.assertEqual(archived.status_code, 302)
        self.assertEqual(db.session.get(ConnectionsPuzzle, puzzle_id).status, "archived")
        deleted = self.api("DELETE", f"/admin/connections/api/puzzles/{puzzle_id}", token=token)
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNone(db.session.get(ConnectionsPuzzle, puzzle_id))
        self.assertEqual(ConnectionsCategory.query.count(), 0)
        self.assertEqual(ConnectionsWord.query.count(), 0)

    def test_admin_mutations_require_csrf_and_regular_user_cannot_modify(self):
        self.login(self.admin)
        token = self.csrf_token()
        no_csrf = self.api("POST", "/admin/connections/api/puzzles", self.payload())
        self.assertEqual(no_csrf.status_code, 400)
        created = self.api("POST", "/admin/connections/api/puzzles", self.payload(), token)
        puzzle_id = created.get_json()["puzzle"]["id"]
        self.login(self.member)
        denied = self.api("DELETE", f"/admin/connections/api/puzzles/{puzzle_id}", token=token)
        self.assertEqual(denied.status_code, 403)
        self.assertIsNotNone(db.session.get(ConnectionsPuzzle, puzzle_id))

    def test_is_admin_defaults_to_false_for_new_accounts(self):
        regular = self.create_user("default-role")
        self.assertFalse(regular.is_admin)

    def test_html_editor_saves_an_incomplete_draft_and_renders_existing_values(self):
        self.login(self.admin)
        token = self.csrf_token()
        editor = self.client.get("/admin/connections/puzzles/new")
        self.assertEqual(editor.status_code, 200)
        self.assertIn(b"Category 4 word 4", editor.data)
        form = {"csrf_token": token, "title": "Draft from editor", "language": "en", "status": "draft"}
        form.update({
            "category_0_name": "Pets",
            "category_0_difficulty": "yellow",
            "category_0_word_0": "DOG",
            "category_0_word_1": "CAT",
        })
        saved = self.client.post("/admin/connections/puzzles/new", data=form)
        self.assertEqual(saved.status_code, 302)
        puzzle = ConnectionsPuzzle.query.filter_by(title="Draft from editor").one()
        self.assertEqual(puzzle.status, "draft")
        self.assertEqual(len(puzzle.categories), 4)
        edit = self.client.get(f"/admin/connections/puzzles/{puzzle.id}/edit")
        self.assertEqual(edit.status_code, 200)
        self.assertIn(b'value="DOG"', edit.data)

    def test_delete_is_blocked_when_a_future_result_foreign_key_references_puzzle(self):
        self.login(self.admin)
        token = self.csrf_token()
        created = self.api("POST", "/admin/connections/api/puzzles", self.payload(), token)
        puzzle_id = created.get_json()["puzzle"]["id"]
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE connections_test_result (id INTEGER PRIMARY KEY, puzzle_id INTEGER NOT NULL REFERENCES connections_puzzle(id))"
            )
            connection.exec_driver_sql(
                "INSERT INTO connections_test_result (puzzle_id) VALUES (?)", (puzzle_id,)
            )
        response = self.api("DELETE", f"/admin/connections/api/puzzles/{puzzle_id}", token=token)
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.session.get(ConnectionsPuzzle, puzzle_id))
        with db.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE connections_test_result")

    def test_cli_admin_grant_requires_operator_confirmation(self):
        runner = app.test_cli_runner()
        declined = runner.invoke(args=["grant-connections-admin", self.member.email], input="n\n")
        self.assertEqual(declined.exit_code, 0, declined.output)
        db.session.refresh(self.member)
        self.assertFalse(self.member.is_admin)
        granted = runner.invoke(args=["grant-connections-admin", self.member.email], input="y\n")
        self.assertEqual(granted.exit_code, 0, granted.output)
        db.session.refresh(self.member)
        self.assertTrue(self.member.is_admin)

    def test_additive_schema_upgrade_adds_admin_flag_to_existing_user_table(self):
        path = Path(__file__).parent / ".connections-admin-migration.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE user (id INTEGER PRIMARY KEY, google_id VARCHAR(100), email VARCHAR(100), name VARCHAR(100))"
                )
                connection.exec_driver_sql("INSERT INTO user VALUES (1, 'old-account', 'old@example.test', 'Old Player')")
            upgrade_pvp_schema(engine)
            columns = {column["name"] for column in inspect(engine).get_columns("user")}
            self.assertIn("is_admin", columns)
            with engine.connect() as connection:
                row = connection.execute(text("SELECT id, name, is_admin FROM user WHERE id = 1")).one()
            self.assertEqual((row.id, row.name, row.is_admin), (1, "Old Player", 0))
        finally:
            engine.dispose()
            for suffix in ("", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
