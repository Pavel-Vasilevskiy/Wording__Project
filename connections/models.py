from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint

from models import db


class ConnectionsPuzzle(db.Model):
    __tablename__ = "connections_puzzle"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'review', 'approved', 'archived')",
            name="ck_connections_puzzle_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    language = db.Column(db.String(16), nullable=False, default="en", server_default="en", index=True)
    status = db.Column(db.String(16), nullable=False, default="draft", server_default="draft", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)

    creator = db.relationship("User", foreign_keys=[created_by])
    categories = db.relationship(
        "ConnectionsCategory",
        back_populates="puzzle",
        cascade="all, delete-orphan",
        order_by="ConnectionsCategory.display_order",
    )


class ConnectionsCategory(db.Model):
    __tablename__ = "connections_category"
    __table_args__ = (
        CheckConstraint(
            "difficulty IN ('yellow', 'green', 'blue', 'purple')",
            name="ck_connections_category_difficulty",
        ),
        UniqueConstraint("puzzle_id", "display_order", name="uq_connections_category_order"),
    )

    id = db.Column(db.Integer, primary_key=True)
    puzzle_id = db.Column(
        db.Integer,
        db.ForeignKey("connections_puzzle.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(100), nullable=False, default="")
    difficulty = db.Column(db.String(16), nullable=False, default="yellow", server_default="yellow")
    description = db.Column(db.String(500), nullable=False, default="")
    display_order = db.Column(db.Integer, nullable=False)

    puzzle = db.relationship("ConnectionsPuzzle", back_populates="categories")
    words = db.relationship(
        "ConnectionsWord",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="ConnectionsWord.display_order",
    )


class ConnectionsWord(db.Model):
    __tablename__ = "connections_word"
    __table_args__ = (
        UniqueConstraint("category_id", "display_order", name="uq_connections_word_order"),
    )

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(
        db.Integer,
        db.ForeignKey("connections_category.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text = db.Column(db.String(60), nullable=False)
    display_order = db.Column(db.Integer, nullable=False)

    category = db.relationship("ConnectionsCategory", back_populates="words")
