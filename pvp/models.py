import secrets
from datetime import datetime

from sqlalchemy import text

from models import db


class PvPLobby(db.Model):
    """Persistent PvP lobby; game/match state will be modeled separately."""

    __tablename__ = "pvp_lobby"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=lambda: secrets.token_hex(16))
    leader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="open", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    words_per_match = db.Column(db.Integer, nullable=False, default=5, server_default=text("5"))
    mode = db.Column(db.String(20), nullable=False, default="timer", server_default=text("'timer'"))
    timer_seconds = db.Column(db.Integer, nullable=False, default=120, server_default=text("120"))
    settings_version = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))

    memberships = db.relationship(
        "PvPLobbyMember",
        back_populates="lobby",
        cascade="all, delete-orphan",
        order_by="(PvPLobbyMember.joined_at, PvPLobbyMember.user_id)",
    )
    leader = db.relationship("User", foreign_keys=[leader_id])
    match = db.relationship(
        "PvPMatch", back_populates="lobby", uselist=False, cascade="all, delete-orphan"
    )


class PvPLobbyMember(db.Model):
    """Current membership only; the global unique user constraint enforces one lobby per user."""

    __tablename__ = "pvp_lobby_member"
    __table_args__ = (db.UniqueConstraint("user_id", name="uq_pvp_member_one_lobby"),)

    lobby_id = db.Column(db.Integer, db.ForeignKey("pvp_lobby.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_ready = db.Column(db.Boolean, nullable=False, default=False, server_default=text("0"))
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    lobby = db.relationship("PvPLobby", back_populates="memberships")
    user = db.relationship("User")


class PvPLobbyKickNotice(db.Model):
    """Short-lived notice allowing a removed member to understand access loss."""

    __tablename__ = "pvp_lobby_kick_notice"

    public_id = db.Column(db.String(32), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PvPMatch(db.Model):
    """A fixed roster and shared word sequence created from one lobby."""

    __tablename__ = "pvp_match"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=lambda: secrets.token_hex(16))
    lobby_id = db.Column(db.Integer, db.ForeignKey("pvp_lobby.id"), nullable=False, unique=True)
    status = db.Column(db.String(16), nullable=False, default="countdown", index=True)
    game_mode = db.Column(db.String(20), nullable=False)
    words_count = db.Column(db.Integer, nullable=False)
    timer_seconds = db.Column(db.Integer)
    attempts_per_word = db.Column(db.Integer, nullable=False, default=6)
    current_round = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    countdown_started_at = db.Column(db.DateTime, nullable=False)
    scheduled_start_at = db.Column(db.DateTime, nullable=False)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    lobby = db.relationship("PvPLobby", back_populates="match")
    rounds = db.relationship(
        "PvPMatchRound", back_populates="match", cascade="all, delete-orphan",
        order_by="PvPMatchRound.round_number",
    )
    players = db.relationship(
        "PvPMatchPlayer", back_populates="match", cascade="all, delete-orphan",
        order_by="(PvPMatchPlayer.joined_at, PvPMatchPlayer.user_id)",
    )


class PvPMatchRound(db.Model):
    """Server-owned answer and timing for a single match round."""

    __tablename__ = "pvp_match_round"
    __table_args__ = (db.UniqueConstraint("match_id", "round_number", name="uq_pvp_match_round_number"),)

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("pvp_match.id", ondelete="CASCADE"), nullable=False, index=True)
    round_number = db.Column(db.Integer, nullable=False)
    word = db.Column(db.String(5), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="waiting")
    started_at = db.Column(db.DateTime)
    deadline_at = db.Column(db.DateTime)

    match = db.relationship("PvPMatch", back_populates="rounds")


class PvPMatchPlayer(db.Model):
    """Per-player match state, independent from mutable lobby membership."""

    __tablename__ = "pvp_match_player"
    __table_args__ = (db.UniqueConstraint("match_id", "user_id", name="uq_pvp_match_player"),)

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("pvp_match.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(16), nullable=False, default="active")
    current_round = db.Column(db.Integer, nullable=False, default=1)
    attempts_used = db.Column(db.Integer, nullable=False, default=0)
    has_solved = db.Column(db.Boolean, nullable=False, default=False, server_default=text("0"))
    # Progress stores only color rows; submitted text belongs in a private future field/API.
    progress = db.Column(db.Text, nullable=False, default="[]", server_default=text("'[]'"))
    round_history = db.Column(db.Text, nullable=False, default="[]", server_default=text("'[]'"))

    match = db.relationship("PvPMatch", back_populates="players")
    user = db.relationship("User")
