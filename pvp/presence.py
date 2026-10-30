"""Server-clock presence tracking and request-driven abandoned-lobby cleanup."""

from datetime import datetime, timedelta

from models import db
from pvp.models import PvPLobby, PvPLobbyKickNotice, PvPLobbyMember


HEARTBEAT_INTERVAL_SECONDS = 10
OFFLINE_AFTER_SECONDS = 45


def is_online(member, now=None):
    now = now or datetime.utcnow()
    return bool(member.last_seen_at and member.last_seen_at > now - timedelta(seconds=OFFLINE_AFTER_SECONDS))


def cleanup_stale_lobbies(now=None):
    """Apply presence transitions inside the caller's write transaction."""
    now = now or datetime.utcnow()
    lobbies = PvPLobby.query.all()
    changed = False
    expired_notices = PvPLobbyKickNotice.query.filter(
        PvPLobbyKickNotice.created_at <= now - timedelta(days=1)
    ).all()
    for notice in expired_notices:
        db.session.delete(notice)
        changed = True

    for lobby in lobbies:
        members = list(lobby.memberships)
        online_members = [member for member in members if is_online(member, now)]
        offline_members = [member for member in members if not is_online(member, now)]

        # A future in_progress lobby represents an actual match. Preserve its
        # roster and game progress when a player disconnects.
        if lobby.status == "in_progress":
            continue

        if not online_members:
            db.session.delete(lobby)
            changed = True
            continue

        for member in offline_members:
            lobby.memberships.remove(member)
            changed = True

        if all(member.user_id != lobby.leader_id for member in online_members):
            next_leader = min(online_members, key=lambda member: (member.joined_at, member.user_id))
            lobby.leader_id = next_leader.user_id
            changed = True

    return changed


def mark_member_online(lobby, user_id, now=None):
    """Refresh one member, resetting stale readiness and transferring a stale leader."""
    now = now or datetime.utcnow()
    member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=user_id).first()
    if member is None:
        return None

    if not is_online(member, now):
        member.is_ready = False
        if lobby.leader_id == user_id:
            online_peers = [
                peer for peer in lobby.memberships
                if peer.user_id != user_id and is_online(peer, now)
            ]
            if online_peers:
                next_leader = min(online_peers, key=lambda peer: (peer.joined_at, peer.user_id))
                lobby.leader_id = next_leader.user_id

    member.last_seen_at = now
    cleanup_stale_lobbies(now)
    return member
