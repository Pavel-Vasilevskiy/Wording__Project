import unittest
from pathlib import Path

from flask import g

from app_with_auth import app
from models import User, db
from pvp.models import PvPPlayerStats


class PvPGlobalLeaderboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".pvp-global-leaderboard-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="pvp-global-leaderboard-test-secret",
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

    def add_player(self, index, score, wins=0, best=None, matches=1, name=None, picture=None):
        user = User(
            google_id=f"pvp-rank-google-{index}",
            email=f"private-{index}@example.test",
            name=name or f"Rank Player {index:02d}",
            picture=picture,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(PvPPlayerStats(
            user_id=user.id,
            matches_played=matches,
            wins=wins,
            total_score=score,
            words_solved=0,
            total_attempts=0,
            solved_words_count=0,
            best_match_score=score if best is None else best,
        ))
        db.session.commit()
        return user

    def login(self, client, user):
        g.pop("_login_user", None)
        with client.session_transaction() as saved:
            saved["_user_id"] = str(user.id)
            saved["_fresh"] = True

    def page(self, client, **params):
        g.pop("_login_user", None)
        return client.get("/pvp/leaderboard", query_string=params)

    def test_public_and_authenticated_access_and_empty_state(self):
        client = app.test_client()
        response = self.page(client)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Рейтинг поки порожній".encode(), response.data)
        self.assertNotIn("Ти ще не зіграв".encode(), response.data)

        user = self.add_player(1, 10)
        self.login(client, user)
        response = self.page(client)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'href="/profile/{user.id}"'.encode(), response.data)
        self.assertIn("Rank Player 01".encode(), response.data)
        no_stats_user = User(google_id="pvp-rank-empty", email="empty@example.test", name="No games yet")
        db.session.add(no_stats_user)
        db.session.commit()
        self.login(client, no_stats_user)
        self.assertIn("Ти ще не зіграв".encode(), self.page(client).data)

    def test_sorting_tie_breakers_and_excludes_users_without_completed_matches(self):
        low_id_tie = self.add_player(10, 100, wins=2, best=70)
        self.add_player(11, 100, wins=2, best=70)
        more_wins = self.add_player(12, 100, wins=3, best=1)
        top = self.add_player(13, 200)
        self.add_player(14, 9999, matches=0)
        page = self.page(app.test_client()).get_data(as_text=True)
        self.assertLess(page.index("Rank Player 13"), page.index("Rank Player 12"))
        self.assertLess(page.index("Rank Player 12"), page.index(low_id_tie.name))
        self.assertLess(page.index(low_id_tie.name), page.index("Rank Player 11"))
        self.assertNotIn("Rank Player 14", page)
        self.assertIn("#1", page)
        self.assertIn("#5", page)
        self.assertEqual(top.name, "Rank Player 13")

    def test_case_insensitive_search_preserves_global_rank_and_paginates(self):
        for index in range(45):
            self.add_player(index, 1000 - index, name=("Needle" if index == 24 else f"Player {index:02d}"))
        client = app.test_client()
        search = self.page(client, q="nEeDlE")
        self.assertEqual(search.status_code, 200)
        self.assertIn("#25", search.get_data(as_text=True))
        self.assertIn("Сторінка 1 з 1", search.get_data(as_text=True))

        first = self.page(client, page="1")
        self.assertIn("Сторінка 1 з 3", first.get_data(as_text=True))
        next_page = self.page(client, page="2", q="Player")
        self.assertIn("Сторінка 2 з 3", next_page.get_data(as_text=True))
        self.assertIn("q=Player", next_page.get_data(as_text=True))
        self.assertIn("#21", next_page.get_data(as_text=True))

    def test_invalid_pages_search_length_and_like_literals_are_safe(self):
        self.add_player(1, 100, name="100%_Real\\Name")
        client = app.test_client()
        self.assertEqual(self.page(client, page="-8").status_code, 200)
        self.assertIn("#1", self.page(client, page="garbage").get_data(as_text=True))
        literal = self.page(client, q="100%_")
        self.assertIn("100%_Real\\Name", literal.get_data(as_text=True))
        too_long = self.page(client, q="x" * 100)
        self.assertEqual(too_long.status_code, 200)

    def test_current_user_rank_shows_off_page_without_duplicate_and_private_data_is_omitted(self):
        users = [self.add_player(i, 100 - i) for i in range(1, 24)]
        client = app.test_client()
        self.login(client, users[-1])
        response = self.page(client, page="1")
        body = response.get_data(as_text=True)
        self.assertIn("Твоє місце: <b>#23</b>", body)
        self.assertEqual(body.count('class="own-rank"'), 1)
        self.assertNotIn("private-23@example.test", body)
        self.assertNotIn(users[-1].google_id, body)

        on_page_client = app.test_client()
        self.login(on_page_client, users[0])
        on_page = self.page(on_page_client, page="1").get_data(as_text=True)
        self.assertNotIn("Твоє місце", on_page)
        self.assertNotIn('class="own-rank"', on_page)

    def test_nickname_avatar_fallback_and_navigation_links_render(self):
        self.add_player(1, 20, name="Friendly Player", picture="https://example.test/avatar.png")
        body = self.page(app.test_client()).get_data(as_text=True)
        self.assertIn("Friendly Player", body)
        self.assertIn("https://example.test/avatar.png", body)
        self.add_player(2, 10, name="No Avatar")
        body = self.page(app.test_client()).get_data(as_text=True)
        self.assertIn("player-fallback", body)
        self.assertNotIn('class="portal-site-links"', body)

    def test_mobile_compact_rows_are_collapsed_and_expandable_without_affecting_desktop_table(self):
        for index in range(22):
            self.add_player(index, 1000 - index, name=("A very long player nickname that should truncate" if index == 0 else None))
        body = self.page(app.test_client()).get_data(as_text=True)
        mobile_rows = body.split('<div class="mobile-leaderboard"', 1)[1].split('</div>\n        {% elif search %}', 1)[0]
        self.assertEqual(mobile_rows.count('<details class="mobile-player'), 20)
        self.assertNotIn(" open", mobile_rows)
        self.assertIn('class="mobile-player rank-1"', mobile_rows)
        self.assertIn('class="mobile-player rank-2"', mobile_rows)
        self.assertIn('class="mobile-player rank-3"', mobile_rows)
        self.assertIn("<summary class=\"mobile-summary\">", mobile_rows)
        self.assertIn("mobile-chevron", mobile_rows)
        self.assertIn("Найкращий результат за матч", mobile_rows)
        self.assertIn("mobile-nickname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;", body)
        self.assertIn("@media (max-width: 620px)", body)
        self.assertIn(".leaderboard-scroll { display: none; }", body)
        self.assertIn(".mobile-leaderboard { display: grid;", body)
        self.assertIn("@media (prefers-reduced-motion: reduce)", body)


if __name__ == "__main__":
    unittest.main()
