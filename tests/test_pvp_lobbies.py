import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app_with_auth import app
from flask import g
from models import User, db
from pvp.models import PvPLobby, PvPLobbyMember
from pvp.routes import MAX_LOBBY_SIZE


class PvPLobbyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".pvp-lobbies-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="pvp-test-secret",
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

    def create_user(self, index, name=None):
        user = User(
            google_id=f"pvp-google-{index}",
            email=f"pvp-{index}@example.test",
            name=name or f"Player {index}",
        )
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, client, user):
        g.pop("_login_user", None)
        with client.session_transaction() as saved_session:
            saved_session["_user_id"] = str(user.id)
            saved_session["_fresh"] = True

    def token(self, client):
        g.pop("_login_user", None)
        client.get("/pvp/")
        with client.session_transaction() as saved_session:
            return saved_session["pvp_csrf_token"]

    def post_form(self, client, path):
        g.pop("_login_user", None)
        return client.post(path, data={"csrf_token": self.token(client)}, follow_redirects=False)

    def get(self, client, path):
        g.pop("_login_user", None)
        return client.get(path)

    def create_lobby(self, client):
        response = self.post_form(client, "/pvp/create")
        self.assertEqual(response.status_code, 302)
        return response.headers["Location"]

    def test_pvp_routes_require_authentication_and_classic_route_is_unchanged(self):
        client = app.test_client()
        self.assertEqual(client.get("/pvp/").status_code, 302)
        self.assertEqual(client.post("/pvp/create").status_code, 302)
        self.assertEqual(client.get("/pvp/" + "a" * 32 + "/state").status_code, 302)
        self.assertEqual(client.get("/wordle").status_code, 302)

    def test_create_lobby_makes_creator_first_member_and_redirects_to_private_detail(self):
        leader = self.create_user(1)
        client = app.test_client()
        self.login(client, leader)

        location = self.create_lobby(client)
        lobby = PvPLobby.query.one()
        self.assertIn(lobby.public_id, location)
        self.assertEqual(lobby.leader_id, leader.id)
        self.assertEqual(lobby.status, "open")
        self.assertEqual([(m.user_id, m.lobby_id) for m in lobby.memberships], [(leader.id, lobby.id)])
        detail = self.get(client, location)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"data-pvp-lobby-state", detail.data)
        self.assertIn(b"pvp_lobby.js", detail.data)
        self.assertIn(b"id=\"pvp-members\"", detail.data)
        leave_url = f"/pvp/{lobby.public_id}/leave"
        self.assertIn(f'<form class="pvp-return-form" method="post" action="{leave_url}">'.encode(), detail.data)
        self.assertIn("← До списку лобі".encode(), detail.data)
        self.assertNotIn("Залишити лобі".encode(), detail.data)
        self.assertEqual(self.create_lobby(client), location)
        self.assertEqual(PvPLobby.query.count(), 1)
        self.assertEqual(self.get(client, leave_url).status_code, 405)
        self.assertEqual(PvPLobbyMember.query.filter_by(user_id=leader.id).count(), 1)
        left = self.post_form(client, leave_url)
        self.assertEqual(left.status_code, 302)
        self.assertEqual(left.headers["Location"], "/pvp/")
        self.assertIsNone(PvPLobbyMember.query.filter_by(user_id=leader.id).first())
        repeated_leave = self.post_form(client, leave_url)
        self.assertEqual(repeated_leave.status_code, 302)
        self.assertEqual(repeated_leave.headers["Location"], "/pvp/")

    def test_join_duplicate_membership_one_lobby_rule_and_member_details(self):
        leader = self.create_user(2)
        guest = self.create_user(3)
        first_client = app.test_client()
        guest_client = app.test_client()
        self.login(first_client, leader)
        self.login(guest_client, guest)
        detail_url = self.create_lobby(first_client)
        lobby = PvPLobby.query.one()
        join_url = f"/pvp/{lobby.public_id}/join"

        self.post_form(guest_client, join_url)
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id).count(), 2)
        self.assertEqual(self.get(guest_client, detail_url).status_code, 200)
        self.post_form(guest_client, join_url)
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id).count(), 2)

        other_client = app.test_client()
        self.login(other_client, self.create_user(4))
        other_detail = self.create_lobby(other_client)
        other_id = PvPLobby.query.filter(PvPLobby.public_id != lobby.public_id).one().public_id
        response = self.post_form(guest_client, f"/pvp/{other_id}/join")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PvPLobbyMember.query.filter_by(user_id=guest.id).one().lobby_id, lobby.id)

    def test_capacity_is_enforced_and_closed_lobbies_are_not_listed_or_joinable(self):
        users = [self.create_user(100 + i) for i in range(MAX_LOBBY_SIZE + 1)]
        lobby = PvPLobby(leader_id=users[0].id, status="open")
        db.session.add(lobby)
        db.session.flush()
        for user in users[:MAX_LOBBY_SIZE]:
            db.session.add(PvPLobbyMember(lobby_id=lobby.id, user_id=user.id))
        closed = PvPLobby(leader_id=users[-1].id, status="closed")
        db.session.add(closed)
        db.session.flush()
        db.session.add(PvPLobbyMember(lobby_id=closed.id, user_id=users[-1].id))
        db.session.commit()

        full_joiner = app.test_client()
        self.login(full_joiner, self.create_user(200))
        self.post_form(full_joiner, f"/pvp/{lobby.public_id}/join")
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id).count(), MAX_LOBBY_SIZE)

        closed_joiner = app.test_client()
        closed_user = self.create_user(201)
        self.login(closed_joiner, closed_user)
        self.post_form(closed_joiner, f"/pvp/{closed.public_id}/join")
        self.assertIsNone(PvPLobbyMember.query.filter_by(user_id=closed_user.id).first())

        listing = self.get(closed_joiner, "/pvp/").get_data(as_text=True)
        self.assertNotIn(closed.public_id, listing)

    def test_nonmembers_cannot_view_or_leave_lobby(self):
        leader = self.create_user(5)
        outsider = self.create_user(6)
        owner_client = app.test_client()
        outsider_client = app.test_client()
        self.login(owner_client, leader)
        self.login(outsider_client, outsider)
        self.create_lobby(owner_client)
        lobby = PvPLobby.query.one()
        detail = f"/pvp/{lobby.public_id}"

        self.assertEqual(self.get(outsider_client, detail).status_code, 404)
        self.assertEqual(self.post_form(outsider_client, detail + "/leave").status_code, 302)
        self.assertEqual(PvPLobbyMember.query.count(), 1)

    def test_state_endpoint_refreshes_members_and_hides_it_from_nonmembers(self):
        leader = self.create_user(30, "Leader Name")
        guest = self.create_user(31, "Guest Name")
        outsider = self.create_user(32, "Outsider Name")
        leader_client, guest_client, outsider_client = [app.test_client() for _ in range(3)]
        for client, user in zip((leader_client, guest_client, outsider_client), (leader, guest, outsider)):
            self.login(client, user)

        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        public_state_url = f"/pvp/{lobby.public_id}/state"
        self.post_form(guest_client, f"/pvp/{lobby.public_id}/join")

        refreshed = self.get(leader_client, public_state_url)
        payload = refreshed.get_json()
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.headers["Cache-Control"], "no-store")
        self.assertEqual(payload["status"], "open")
        self.assertEqual(payload["leader"], "Leader Name")
        self.assertEqual(payload["member_count"], 2)
        self.assertEqual(payload["max_members"], MAX_LOBBY_SIZE)
        self.assertEqual(payload["members"], [
            {"name": "Leader Name", "is_leader": True},
            {"name": "Guest Name", "is_leader": False},
        ])
        self.assertNotIn("email", str(payload))
        self.assertEqual(self.get(outsider_client, public_state_url).status_code, 404)

        self.post_form(guest_client, f"/pvp/{lobby.public_id}/leave")
        repeated_guest_leave = self.post_form(guest_client, f"/pvp/{lobby.public_id}/leave")
        self.assertEqual(repeated_guest_leave.status_code, 302)
        payload_after_leave = self.get(leader_client, public_state_url).get_json()
        self.assertEqual(payload_after_leave["member_count"], 1)
        self.assertEqual([member["name"] for member in payload_after_leave["members"]], ["Leader Name"])

        self.post_form(leader_client, f"/pvp/{lobby.public_id}/leave")
        missing_lobby = self.get(leader_client, public_state_url)
        self.assertEqual(missing_lobby.status_code, 404)
        self.assertEqual(missing_lobby.get_json(), {"error": "lobby_unavailable"})
        repeated_last_leave = self.post_form(leader_client, f"/pvp/{lobby.public_id}/leave")
        self.assertEqual(repeated_last_leave.status_code, 302)

    def test_nonleader_leave_preserves_leader_and_leader_leave_transfers_by_join_time(self):
        leader, member_a, member_b = [self.create_user(10 + i) for i in range(3)]
        clients = [app.test_client() for _ in range(3)]
        for client, user in zip(clients, (leader, member_a, member_b)):
            self.login(client, user)
        self.create_lobby(clients[0])
        lobby = PvPLobby.query.one()
        for client in clients[1:]:
            self.post_form(client, f"/pvp/{lobby.public_id}/join")

        # Make the expected next leader unambiguous even if the joins share clock precision.
        member_a_row = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=member_a.id).one()
        member_b_row = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=member_b.id).one()
        member_a_row.joined_at = datetime.utcnow() - timedelta(seconds=2)
        member_b_row.joined_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

        self.post_form(clients[1], f"/pvp/{lobby.public_id}/leave")
        self.assertEqual(db.session.get(PvPLobby, lobby.id).leader_id, leader.id)
        self.post_form(clients[0], f"/pvp/{lobby.public_id}/leave")
        self.assertEqual(db.session.get(PvPLobby, lobby.id).leader_id, member_b.id)
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id).count(), 1)

    def test_empty_lobby_is_deleted_and_create_requires_csrf(self):
        user = self.create_user(20)
        client = app.test_client()
        self.login(client, user)
        self.assertEqual(client.post("/pvp/create").status_code, 400)
        self.create_lobby(client)
        lobby = PvPLobby.query.one()
        self.post_form(client, f"/pvp/{lobby.public_id}/leave")
        self.assertEqual(PvPLobby.query.count(), 0)
        self.assertEqual(PvPLobbyMember.query.count(), 0)

    def test_my_lobby_action_and_open_lobby_heading_follow_document_flow(self):
        leader = self.create_user(40)
        client = app.test_client()
        self.login(client, leader)
        self.create_lobby(client)

        page = self.get(client, "/pvp/").get_data(as_text=True)
        wrapper_start = page.index('<div class="pvp-current-lobby">')
        action_index = page.index("Відкрити моє лобі", wrapper_start)
        wrapper_end = page.index("</div>", action_index)
        heading_index = page.index('class="pvp-section-title">Відкриті лобі')
        self.assertLess(wrapper_start, action_index)
        self.assertLess(action_index, wrapper_end)
        self.assertLess(wrapper_end, heading_index)


if __name__ == "__main__":
    unittest.main()
