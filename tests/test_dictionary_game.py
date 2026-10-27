import unittest
from pathlib import Path

import app as basic_app_module
import app_production as production_app_module
from app_with_auth import app as authenticated_app, pick_word
from models import db, GameSession, User
from word_dictionary import (
    DictionaryLoadError,
    EN_US_DICTIONARY,
    WordDictionary,
    load_dictionary,
)


class DictionaryGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".leaderboard-test.sqlite"
        authenticated_app.config.update(
            TESTING=True,
            SECRET_KEY="test-only-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{cls.database_path}",
        )

    @classmethod
    def tearDownClass(cls):
        with authenticated_app.app_context():
            db.session.remove()
            db.engine.dispose()

    def setUp(self):
        self.app_context = authenticated_app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        self.app_context.pop()

    def create_user(self, google_id="dictionary-test", name="Dictionary Tester"):
        user = User(
            google_id=google_id,
            email=f"{google_id}@example.test",
            name=name,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, client, user, target_word="arise"):
        with client.session_transaction() as game_session:
            game_session["_user_id"] = str(user.id)
            game_session["_fresh"] = True
            game_session["target_word"] = target_word.upper()
            game_session["tries"] = 0
            game_session["guesses"] = []
            game_session["game_over"] = False
            game_session["won"] = False

    def test_large_dictionary_is_normalized_and_cached_once(self):
        self.assertEqual(len(EN_US_DICTIONARY), 8754)
        self.assertIs(load_dictionary("en-US"), EN_US_DICTIONARY)
        self.assertTrue(EN_US_DICTIONARY.contains("CRANE"))
        self.assertEqual(EN_US_DICTIONARY.words, tuple(sorted(EN_US_DICTIONARY.words)))
        self.assertTrue(all(word.isascii() and word.isalpha() and len(word) == 5 for word in EN_US_DICTIONARY.words))

    def test_valid_word_is_accepted_and_existing_scoring_response_is_unchanged(self):
        user = self.create_user()
        client = authenticated_app.test_client()
        self.login(client, user)

        response = client.post("/guess", json={"guess": "crane"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["tries"], 1)
        self.assertEqual([tile["status"] for tile in payload["result"]], ["gray", "green", "yellow", "gray", "green"])
        self.assertFalse(payload["game_over"])
        self.assertIsNone(payload["target_word"])

    def test_uppercase_and_lowercase_guesses_are_normalized(self):
        for suffix, guess in (("upper", "CRANE"), ("lower", "crane")):
            with self.subTest(guess=guess):
                user = self.create_user(f"google-{suffix}", suffix)
                client = authenticated_app.test_client()
                self.login(client, user, target_word="crane")

                response = client.post("/guess", json={"guess": guess})

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["won"])
                self.assertEqual(response.get_json()["result"][0]["letter"], "C")
                self.assertEqual(response.get_json()["tries"], 1)

    def test_non_dictionary_word_is_rejected_without_changing_game_or_stats(self):
        user = self.create_user()
        client = authenticated_app.test_client()
        self.login(client, user)

        response = client.post("/guess", json={"guess": "qzxjk"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Not in word list"})
        with client.session_transaction() as game_session:
            self.assertEqual(game_session["tries"], 0)
            self.assertEqual(game_session["guesses"], [])
            self.assertEqual(game_session["target_word"], "ARISE")
        self.assertEqual(user.games_played, 0)
        self.assertEqual(GameSession.query.filter_by(user_id=user.id).count(), 0)
        leaderboard = client.get("/leaderboard")
        self.assertEqual(leaderboard.status_code, 200)
        self.assertNotIn("Dictionary Tester", leaderboard.get_data(as_text=True))

    def test_wrong_lengths_and_non_ascii_or_nonletter_input_are_rejected_without_attempts(self):
        user = self.create_user()
        client = authenticated_app.test_client()
        self.login(client, user)
        invalid_guesses = ("four", "longer", "a1cde", "ab cde", "abc-d", "éabcd", "Krane")

        for guess in invalid_guesses:
            with self.subTest(guess=guess):
                response = client.post("/guess", json={"guess": guess})
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                with client.session_transaction() as game_session:
                    self.assertEqual(game_session["tries"], 0)
                    self.assertEqual(game_session["guesses"], [])

        valid_response = client.post("/guess", json={"guess": "CRANE"})
        self.assertEqual(valid_response.status_code, 200)
        self.assertEqual(valid_response.get_json()["tries"], 1)
        self.assertEqual(user.games_played, 0)
        self.assertEqual(GameSession.query.filter_by(user_id=user.id).count(), 0)

    def test_every_server_entry_point_uses_the_dictionary_validator(self):
        for module in (basic_app_module, production_app_module):
            with self.subTest(app=module.__name__):
                client = module.app.test_client()
                with client.session_transaction() as game_session:
                    game_session["target_word"] = "ARISE"
                    game_session["tries"] = 0
                    game_session["guesses"] = []

                invalid = client.post("/guess", json={"guess": "qzxjk"})
                self.assertEqual(invalid.status_code, 400)
                self.assertEqual(invalid.get_json(), {"error": "Not in word list"})
                with client.session_transaction() as game_session:
                    self.assertEqual(game_session["tries"], 0)

                valid = client.post("/guess", json={"guess": "crane"})
                self.assertEqual(valid.status_code, 200)
                self.assertEqual(valid.get_json()["tries"], 1)

    def test_answer_is_selected_from_dictionary_and_stays_constant_in_game(self):
        for _ in range(100):
            self.assertTrue(EN_US_DICTIONARY.contains(pick_word()))

        user = self.create_user()
        client = authenticated_app.test_client()
        self.login(client, user, target_word="arise")
        first = client.post("/guess", json={"guess": "crane"})
        self.assertEqual(first.status_code, 200)
        self.assertIsNone(first.get_json()["target_word"])

        page = client.get("/wordle")
        self.assertEqual(page.status_code, 200)
        with client.session_transaction() as game_session:
            self.assertEqual(game_session["target_word"], "ARISE")

        client.post("/new_game")
        with client.session_transaction() as game_session:
            self.assertTrue(EN_US_DICTIONARY.contains(game_session["target_word"]))

    def test_dictionary_loader_filters_invalid_rows_deduplicates_and_fails_clearly(self):
        source = Path(__file__).with_name(".dictionary-source-test.txt")
        empty_source = Path(__file__).with_name(".dictionary-empty-test.txt")
        try:
            source.write_text(
                "CRANE\ncrane\nARISE\nsp ace\nabc-d\n12345\néabcd\nKrane\nTOOLONG\n",
                encoding="utf-8",
            )
            loaded = WordDictionary.from_file(source)
            self.assertEqual(loaded.words, ("arise", "crane"))

            with self.assertRaisesRegex(DictionaryLoadError, "Could not load"):
                WordDictionary.from_file(source.with_name(".dictionary-missing-test.txt"))

            empty_source.write_text("only invalid rows: 12345\n", encoding="utf-8")
            with self.assertRaisesRegex(DictionaryLoadError, "no valid five-letter words"):
                WordDictionary.from_file(empty_source)

            empty_source.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(DictionaryLoadError, "Could not load"):
                WordDictionary.from_file(empty_source)
        finally:
            source.unlink(missing_ok=True)
            empty_source.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
