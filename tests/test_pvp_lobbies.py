import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app_with_auth import app
from flask import g
from models import User, db
from pvp.models import PvPLobby, PvPLobbyKickNotice, PvPLobbyMember, PvPMatch
from pvp.presence import HEARTBEAT_INTERVAL_SECONDS, OFFLINE_AFTER_SECONDS, cleanup_stale_lobbies, is_online
from pvp.routes import MAX_LOBBY_SIZE
from pvp.schema import upgrade_pvp_schema
from sqlalchemy import create_engine, inspect, text


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

    def post_form(self, client, path, **fields):
        g.pop("_login_user", None)
        return client.post(
            path,
            data={"csrf_token": self.token(client), **fields},
            follow_redirects=False,
        )

    def post_form_with_existing_token(self, client, path, **fields):
        with client.session_transaction() as saved_session:
            csrf_token = saved_session["pvp_csrf_token"]
        g.pop("_login_user", None)
        return client.post(path, data={"csrf_token": csrf_token, **fields}, follow_redirects=False)

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
        self.assertEqual(lobby.words_per_match, 5)
        self.assertEqual(lobby.mode, "timer")
        self.assertEqual(lobby.timer_seconds, 120)
        self.assertEqual(lobby.settings_version, 0)
        self.assertEqual([(m.user_id, m.lobby_id) for m in lobby.memberships], [(leader.id, lobby.id)])
        self.assertFalse(lobby.memberships[0].is_ready)
        detail = self.get(client, location)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"data-pvp-lobby-state", detail.data)
        self.assertIn(b"pvp_lobby.js", detail.data)
        self.assertIn(b'id="pvp-members"', detail.data)
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

    def test_lobby_list_refresh_returns_new_rooms_and_excludes_deleted_rooms(self):
        owner, watcher = self.create_user(101), self.create_user(102)
        owner_client, watcher_client = app.test_client(), app.test_client()
        self.login(owner_client, owner)
        self.login(watcher_client, watcher)
        self.get(watcher_client, "/pvp/")
        self.create_lobby(owner_client)
        lobby = PvPLobby.query.one()

        refresh = self.post_form(watcher_client, "/pvp/list-state")
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual([item["id"] for item in refresh.get_json()["lobbies"]], [lobby.public_id])
        self.post_form(owner_client, f"/pvp/{lobby.public_id}/leave")
        refresh = self.post_form_with_existing_token(watcher_client, "/pvp/list-state")
        self.assertEqual(refresh.get_json()["lobbies"], [])

    def test_missing_lobby_detail_redirects_with_clear_message(self):
        user = self.create_user(103)
        client = app.test_client()
        self.login(client, user)
        page = self.get(client, "/pvp/" + "a" * 32)
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].endswith("/pvp/"))
        destination = self.get(client, page.headers["Location"])
        self.assertIn("Це лобі більше не існує. Можливо, його було закрито", destination.get_data(as_text=True))

    def test_leader_can_kick_member_and_member_loses_access_idempotently(self):
        leader, player = self.create_user(104), self.create_user(105)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        state = self.get(leader_client, f"/pvp/{lobby.public_id}/state").get_json()
        token = next(member["kick_token"] for member in state["members"] if member["name"] == player.name)

        kicked = self.post_form(leader_client, f"/pvp/{lobby.public_id}/kick", member_token=token)
        self.assertEqual(kicked.status_code, 200)
        self.assertTrue(kicked.get_json()["removed"])
        self.assertEqual(self.get(player_client, f"/pvp/{lobby.public_id}/state").status_code, 410)
        detail = self.get(player_client, f"/pvp/{lobby.public_id}")
        self.assertEqual(detail.status_code, 302)
        landing = self.get(player_client, detail.headers["Location"])
        self.assertIn("Лідер видалив вас із цього лобі", landing.get_data(as_text=True))
        repeated = self.post_form(leader_client, f"/pvp/{lobby.public_id}/kick", member_token=token)
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.get_json()["removed"])
        self.assertIsNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).first())

    def test_nonleader_cannot_kick_and_leader_self_kick_token_is_not_issued(self):
        leader, player = self.create_user(106), self.create_user(107)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        state = self.get(player_client, f"/pvp/{lobby.public_id}/state").get_json()
        self.assertNotIn("kick_token", state["members"][0])
        denied = self.post_form(player_client, f"/pvp/{lobby.public_id}/kick", member_token="a" * 64)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id).count(), 2)

    def test_offline_members_are_removed_before_match_and_match_roster_is_preserved(self):
        leader, player = self.create_user(108), self.create_user(109)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        stale_at = datetime(2026, 1, 1, 0, 0, 0)
        now = stale_at + timedelta(seconds=OFFLINE_AFTER_SECONDS)
        stale_member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).one()
        stale_member.last_seen_at = stale_at
        db.session.commit()

        cleanup_stale_lobbies(now=now)
        db.session.commit()
        self.assertIsNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).first())

        # A real attached match owns a fixed roster; offline cleanup keeps membership/history.
        lobby.status = "matched"
        restored = PvPLobbyMember(lobby_id=lobby.id, user_id=player.id, last_seen_at=stale_at)
        db.session.add(restored)
        db.session.flush()
        match = PvPMatch(
            lobby_id=lobby.id, status="in_progress", game_mode="timer", words_count=3,
            timer_seconds=120, attempts_per_word=6, current_round=1,
            countdown_started_at=stale_at, scheduled_start_at=stale_at,
        )
        db.session.add(match)
        db.session.commit()
        cleanup_stale_lobbies(now=now + timedelta(seconds=1))
        db.session.commit()
        self.assertIsNotNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).first())
        self.assertEqual(PvPMatch.query.filter_by(lobby_id=lobby.id).count(), 1)

    def test_heartbeat_interval_and_server_timeout_boundary(self):
        self.assertEqual(HEARTBEAT_INTERVAL_SECONDS, 10)
        self.assertEqual(OFFLINE_AFTER_SECONDS, 45)
        user = self.create_user(110)
        client = app.test_client()
        self.login(client, user)
        self.create_lobby(client)
        member = PvPLobbyMember.query.one()
        base = datetime(2026, 1, 1, 12, 0, 0)
        member.last_seen_at = base
        self.assertTrue(is_online(member, now=base + timedelta(seconds=44, milliseconds=999)))
        self.assertFalse(is_online(member, now=base + timedelta(seconds=45)))
        payload = self.get(client, f"/pvp/{PvPLobby.query.one().public_id}/state").get_json()
        self.assertEqual(payload["heartbeat_interval_seconds"], 10)

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

        self.assertEqual(self.get(outsider_client, detail).status_code, 302)
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
        self.assertEqual(payload["settings"], {
            "words_per_match": 5,
            "mode": "timer",
            "timer_seconds": 120,
            "version": 0,
            "attempts_per_word": 6,
        })
        self.assertFalse(payload["can_start"])
        self.assertEqual([{key: member[key] for key in ("name", "is_leader", "is_online", "is_ready")} for member in payload["members"]], [
            {"name": "Leader Name", "is_leader": True, "is_online": True, "is_ready": False},
            {"name": "Guest Name", "is_leader": False, "is_online": True, "is_ready": False},
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

    def test_leader_settings_are_validated_and_changes_reset_everyone_ready(self):
        leader, player = self.create_user(50), self.create_user(51)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        for client in (leader_client, player_client):
            self.post_form(client, f"/pvp/{lobby.public_id}/ready", ready="true")
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, is_ready=True).count(), 2)

        self.post_form(
            leader_client,
            f"/pvp/{lobby.public_id}/settings",
            words_per_match="10",
            mode="timer",
            timer_seconds="75",
        )
        db.session.refresh(lobby)
        self.assertEqual((lobby.words_per_match, lobby.mode, lobby.timer_seconds), (10, "timer", 75))
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, is_ready=True).count(), 0)

        self.post_form(
            leader_client,
            f"/pvp/{lobby.public_id}/settings",
            words_per_match="3",
            mode="no_limit",
        )
        db.session.refresh(lobby)
        self.assertEqual((lobby.words_per_match, lobby.mode, lobby.timer_seconds), (3, "no_limit", 75))

        self.post_form(
            leader_client,
            f"/pvp/{lobby.public_id}/settings",
            words_per_match="4",
            mode="timer",
            timer_seconds="29",
        )
        db.session.refresh(lobby)
        self.assertEqual((lobby.words_per_match, lobby.mode, lobby.timer_seconds), (3, "no_limit", 75))

    def test_json_settings_save_is_supported_and_unchanged_values_keep_ready(self):
        leader = self.create_user(111)
        client = app.test_client()
        self.login(client, leader)
        self.create_lobby(client)
        lobby = PvPLobby.query.one()
        member = PvPLobbyMember.query.one()
        member.is_ready = True
        db.session.commit()

        response = self.post_form(client, f"/pvp/{lobby.public_id}/settings",
                                  words_per_match="5", mode="timer", timer_seconds="120")
        self.assertEqual(response.status_code, 302)  # Regular forms remain compatible.
        self.assertTrue(member.is_ready)

        with client.session_transaction() as saved_session:
            token = saved_session["pvp_csrf_token"]
        response = client.post(
            f"/pvp/{lobby.public_id}/settings",
            data={"csrf_token": token, "settings_version": "0", "words_per_match": "10", "mode": "timer", "timer_seconds": "90"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["changed"])
        self.assertFalse(member.is_ready)
        self.assertEqual(response.get_json()["settings"]["version"], 1)
        stale = client.post(
            f"/pvp/{lobby.public_id}/settings",
            data={"csrf_token": token, "settings_version": "0", "words_per_match": "3", "mode": "timer", "timer_seconds": "60"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(stale.status_code, 409)
        db.session.refresh(lobby)
        self.assertEqual(lobby.words_per_match, 10)
        self.assertEqual(lobby.settings_version, 1)

    def test_regular_member_cannot_change_settings_or_start_lobby(self):
        leader, player = self.create_user(52), self.create_user(53)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")

        self.post_form(
            player_client,
            f"/pvp/{lobby.public_id}/settings",
            words_per_match="100",
            mode="timer",
            timer_seconds="600",
        )
        self.post_form(player_client, f"/pvp/{lobby.public_id}/ready", ready="true")
        self.post_form(player_client, f"/pvp/{lobby.public_id}/start")
        db.session.refresh(lobby)
        self.assertEqual((lobby.words_per_match, lobby.mode, lobby.timer_seconds), (5, "timer", 120))
        self.assertEqual(lobby.leader_id, leader.id)
        self.assertEqual(lobby.status, "open")

    def test_player_can_toggle_ready_and_start_requires_all_online_and_ready(self):
        leader, player = self.create_user(54), self.create_user(55)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        player_membership = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).one()
        self.assertFalse(player_membership.is_ready)

        self.post_form(player_client, f"/pvp/{lobby.public_id}/ready", ready="true")
        self.assertTrue(player_membership.is_ready)
        self.post_form(player_client, f"/pvp/{lobby.public_id}/ready", ready="false")
        self.assertFalse(player_membership.is_ready)

        self.post_form(player_client, f"/pvp/{lobby.public_id}/ready", ready="true")
        self.post_form(leader_client, f"/pvp/{lobby.public_id}/start")
        db.session.refresh(lobby)
        self.assertEqual(lobby.status, "open")  # Leader has not marked Ready yet.

        leader_membership = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=leader.id).one()
        self.post_form(leader_client, f"/pvp/{lobby.public_id}/ready", ready="true")
        player_membership.last_seen_at = datetime.utcnow() - timedelta(seconds=OFFLINE_AFTER_SECONDS + 1)
        player_membership.is_ready = True
        db.session.commit()
        self.post_form(leader_client, f"/pvp/{lobby.public_id}/start")
        db.session.refresh(lobby)
        self.assertEqual(lobby.status, "open")
        self.assertIsNotNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).first())
        self.post_form_with_existing_token(leader_client, f"/pvp/{lobby.public_id}/heartbeat")
        self.assertIsNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).first())
        self.post_form(leader_client, f"/pvp/{lobby.public_id}/start")
        self.assertEqual(db.session.get(PvPLobby, lobby.id).status, "matched")

    def test_heartbeat_updates_server_time_and_resets_ready_after_offline_period(self):
        leader, player = self.create_user(56), self.create_user(57)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).one()
        before = datetime.utcnow() - timedelta(seconds=OFFLINE_AFTER_SECONDS + 1)
        member.last_seen_at = before
        member.is_ready = True
        db.session.commit()

        heartbeat = self.post_form_with_existing_token(player_client, f"/pvp/{lobby.public_id}/heartbeat")
        self.assertEqual(heartbeat.status_code, 200)
        self.assertTrue(heartbeat.get_json()["online"])
        self.assertGreater(member.last_seen_at, before)
        self.assertFalse(member.is_ready)

    def test_state_shows_offline_without_mutating_ready_then_post_cleanup_resets_it(self):
        leader, player = self.create_user(58), self.create_user(59)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).one()
        member.last_seen_at = datetime.utcnow() - timedelta(seconds=OFFLINE_AFTER_SECONDS + 1)
        member.is_ready = True
        db.session.commit()

        state_url = f"/pvp/{lobby.public_id}/state"
        state = self.get(leader_client, state_url).get_json()
        player_state = next(item for item in state["members"] if item["name"] == player.name)
        self.assertFalse(player_state["is_online"])
        self.assertFalse(player_state["is_ready"])
        self.assertTrue(member.is_ready)  # The state GET is read-only for Ready.

        self.post_form_with_existing_token(leader_client, f"/pvp/{lobby.public_id}/heartbeat")
        self.assertIsNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=player.id).first())

    def test_offline_leader_transfers_to_earliest_joined_online_member(self):
        leader, first_online, second_online = [self.create_user(60 + i) for i in range(3)]
        clients = [app.test_client() for _ in range(3)]
        for client, user in zip(clients, (leader, first_online, second_online)):
            self.login(client, user)
        self.create_lobby(clients[0])
        lobby = PvPLobby.query.one()
        self.post_form(clients[1], f"/pvp/{lobby.public_id}/join")
        self.post_form(clients[2], f"/pvp/{lobby.public_id}/join")
        now = datetime.utcnow()
        PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=leader.id).one().last_seen_at = now - timedelta(seconds=91)
        PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=first_online.id).one().joined_at = now - timedelta(seconds=10)
        PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=second_online.id).one().joined_at = now - timedelta(seconds=5)
        db.session.commit()

        self.post_form_with_existing_token(clients[2], f"/pvp/{lobby.public_id}/heartbeat")
        self.assertEqual(db.session.get(PvPLobby, lobby.id).leader_id, first_online.id)
        self.assertIsNone(PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=leader.id).first())

    def test_all_offline_lobby_is_deleted_during_post_cleanup(self):
        leader, player = self.create_user(70), self.create_user(71)
        leader_client, player_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(player_client, player)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(player_client, f"/pvp/{lobby.public_id}/join")
        stale = datetime.utcnow() - timedelta(seconds=OFFLINE_AFTER_SECONDS + 1)
        for member in lobby.memberships:
            member.last_seen_at = stale
        db.session.commit()

        # GET state remains read-only; a CSRF protected list refresh performs cleanup.
        self.assertEqual(self.get(leader_client, f"/pvp/{lobby.public_id}/state").status_code, 200)
        response = self.post_form_with_existing_token(leader_client, "/pvp/list-state")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(db.session.get(PvPLobby, lobby.id))
        self.assertEqual(PvPLobbyMember.query.filter_by(lobby_id=lobby.id).count(), 0)

    def test_returning_member_heartbeat_precedes_cleanup_and_requires_new_ready(self):
        leader, returning = self.create_user(72), self.create_user(73)
        leader_client, returning_client = app.test_client(), app.test_client()
        self.login(leader_client, leader)
        self.login(returning_client, returning)
        self.create_lobby(leader_client)
        lobby = PvPLobby.query.one()
        self.post_form(returning_client, f"/pvp/{lobby.public_id}/join")
        member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=returning.id).one()
        member.last_seen_at = datetime.utcnow() - timedelta(seconds=OFFLINE_AFTER_SECONDS + 1)
        member.is_ready = True
        leader_member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=leader.id).one()
        leader_member.last_seen_at = member.last_seen_at
        leader_member.is_ready = True
        db.session.commit()

        response = self.post_form_with_existing_token(returning_client, f"/pvp/{lobby.public_id}/heartbeat")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(db.session.get(PvPLobby, lobby.id))
        self.assertFalse(member.is_ready)
        self.assertEqual(db.session.get(PvPLobby, lobby.id).leader_id, returning.id)

    def test_additive_schema_upgrade_preserves_existing_lobby_rows(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE pvp_lobby (id INTEGER PRIMARY KEY, public_id VARCHAR(32) NOT NULL, "
                "leader_id INTEGER NOT NULL, status VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL)"
            ))
            connection.execute(text(
                "CREATE TABLE pvp_lobby_member (lobby_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
                "joined_at DATETIME NOT NULL, PRIMARY KEY (lobby_id, user_id), UNIQUE (user_id))"
            ))
            connection.execute(text(
                "INSERT INTO pvp_lobby VALUES (1, 'a' || 'a', 9, 'open', '2026-01-01 00:00:00')"
            ))
            connection.execute(text(
                "INSERT INTO pvp_lobby_member VALUES (1, 9, '2026-01-01 00:00:00')"
            ))

        upgrade_pvp_schema(engine)
        upgrade_pvp_schema(engine)
        with engine.connect() as connection:
            lobby_columns = {column["name"] for column in inspect(connection).get_columns("pvp_lobby")}
            member_columns = {column["name"] for column in inspect(connection).get_columns("pvp_lobby_member")}
            lobby_row = connection.execute(text(
                "SELECT words_per_match, mode, timer_seconds FROM pvp_lobby WHERE id = 1"
            )).one()
            member_row = connection.execute(text(
                "SELECT is_ready, last_seen_at FROM pvp_lobby_member WHERE lobby_id = 1"
            )).one()
        self.assertTrue({"words_per_match", "mode", "timer_seconds", "settings_version"}.issubset(lobby_columns))
        self.assertTrue({"is_ready", "last_seen_at"}.issubset(member_columns))
        self.assertEqual(tuple(lobby_row), (5, "timer", 120))
        self.assertFalse(member_row.is_ready)
        self.assertNotEqual(str(member_row.last_seen_at), "1970-01-01 00:00:00")
        self.assertEqual(PvPLobby.query.count(), 0)  # Upgrade used an isolated in-memory database.
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
