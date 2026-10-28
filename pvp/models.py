import secrets
from datetime import datetime

from models import db


class PvPLobby(db.Model):
    """Persistent PvP lobby; game/match state will be modeled separately."""

    __tablename__ = "pvp_lobby"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=lambda: secrets.token_hex(16))
    leader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="open", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    memberships = db.relationship(
        "PvPLobbyMember",
        back_populates="lobby",
        cascade="all, delete-orphan",
        order_by="(PvPLobbyMember.joined_at, PvPLobbyMember.user_id)",
    )
    leader = db.relationship("User", foreign_keys=[leader_id])


class PvPLobbyMember(db.Model):
    """Current membership only; the global unique user constraint enforces one lobby per user."""

    __tablename__ = "pvp_lobby_member"
    __table_args__ = (db.UniqueConstraint("user_id", name="uq_pvp_member_one_lobby"),)

    lobby_id = db.Column(db.Integer, db.ForeignKey("pvp_lobby.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    lobby = db.relationship("PvPLobby", back_populates="memberships")
    user = db.relationship("User")
