from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import validates
from sqlalchemy.types import JSON

from models import db


cyberpunk_level_products = db.Table(
    "cyberpunk_level_products",
    db.Column("level_id", db.Integer, db.ForeignKey("cyberpunk_warehouse_level.id", ondelete="CASCADE"), primary_key=True),
    db.Column("product_id", db.Integer, db.ForeignKey("cyberpunk_warehouse_product.id", ondelete="CASCADE"), primary_key=True),
)


class CyberpunkAsset(db.Model):
    __tablename__ = "cyberpunk_warehouse_asset"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('product', 'customer', 'customer_emotion', 'warehouse_background', 'shelf', 'ui', 'animation')",
            name="ck_cyberpunk_asset_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    asset_key = db.Column(db.String(100), nullable=False, unique=True, index=True)
    asset_type = db.Column(db.String(32), nullable=False, index=True)
    storage_path = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(80), nullable=False, default="image/webp", server_default="image/webp")
    alt_text = db.Column(db.String(180), nullable=False, default="", server_default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)

    creator = db.relationship("User", foreign_keys=[created_by])


class CyberpunkProduct(db.Model):
    __tablename__ = "cyberpunk_warehouse_product"
    __table_args__ = (
        CheckConstraint("spawn_weight > 0", name="ck_cyberpunk_product_spawn_weight"),
        CheckConstraint(
            "rarity IN ('common', 'uncommon', 'rare', 'epic', 'legendary')",
            name="ck_cyberpunk_product_rarity",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    internal_key = db.Column(db.String(80), nullable=False, unique=True, index=True)
    category = db.Column(db.String(80), nullable=False, default="", server_default="")
    image_asset_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_asset.id", ondelete="SET NULL"), nullable=True)
    rarity = db.Column(db.String(16), nullable=False, default="common", server_default="common")
    spawn_weight = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    image_asset = db.relationship("CyberpunkAsset", foreign_keys=[image_asset_id])
    levels = db.relationship("CyberpunkLevel", secondary=cyberpunk_level_products, back_populates="products")


class CyberpunkCustomer(db.Model):
    __tablename__ = "cyberpunk_warehouse_customer"
    __table_args__ = (CheckConstraint("default_waiting_seconds >= 0", name="ck_cyberpunk_customer_waiting"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    customer_type = db.Column(db.String(40), nullable=False, default="standard", server_default="standard")
    character_artwork_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_asset.id", ondelete="SET NULL"), nullable=True)
    happy_artwork_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_asset.id", ondelete="SET NULL"), nullable=True)
    impatient_artwork_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_asset.id", ondelete="SET NULL"), nullable=True)
    disappointed_artwork_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_asset.id", ondelete="SET NULL"), nullable=True)
    default_waiting_seconds = db.Column(db.Integer, nullable=False, default=60, server_default="60")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    character_artwork = db.relationship("CyberpunkAsset", foreign_keys=[character_artwork_id])
    happy_artwork = db.relationship("CyberpunkAsset", foreign_keys=[happy_artwork_id])
    impatient_artwork = db.relationship("CyberpunkAsset", foreign_keys=[impatient_artwork_id])
    disappointed_artwork = db.relationship("CyberpunkAsset", foreign_keys=[disappointed_artwork_id])


class CyberpunkGameMode(db.Model):
    __tablename__ = "cyberpunk_warehouse_game_mode"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(300), nullable=False, default="", server_default="")
    configuration_schema = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    default_configuration = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    is_builtin = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    levels = db.relationship("CyberpunkLevel", back_populates="game_mode")


class CyberpunkLevel(db.Model):
    __tablename__ = "cyberpunk_warehouse_level"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_cyberpunk_level_status"),
        CheckConstraint("level_number > 0", name="ck_cyberpunk_level_number_positive"),
        CheckConstraint("time_limit_seconds IS NULL OR time_limit_seconds > 0", name="ck_cyberpunk_level_time_limit"),
        UniqueConstraint("level_number", name="uq_cyberpunk_level_number"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(600), nullable=False, default="", server_default="")
    level_number = db.Column(db.Integer, nullable=False)
    game_mode_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_game_mode.id"), nullable=False, index=True)
    difficulty = db.Column(db.String(32), nullable=False, default="normal", server_default="normal")
    status = db.Column(db.String(16), nullable=False, default="draft", server_default="draft", index=True)
    mode_configuration = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    order_configuration = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    time_limit_seconds = db.Column(db.Integer, nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_customer.id", ondelete="SET NULL"), nullable=True)
    background_asset_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_asset.id", ondelete="SET NULL"), nullable=True)
    shelf_asset_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_asset.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    game_mode = db.relationship("CyberpunkGameMode", back_populates="levels")
    customer = db.relationship("CyberpunkCustomer", foreign_keys=[customer_id])
    background_asset = db.relationship("CyberpunkAsset", foreign_keys=[background_asset_id])
    shelf_asset = db.relationship("CyberpunkAsset", foreign_keys=[shelf_asset_id])
    products = db.relationship("CyberpunkProduct", secondary=cyberpunk_level_products, back_populates="levels")
    orders = db.relationship("CyberpunkOrder", back_populates="level", cascade="all, delete-orphan", order_by="CyberpunkOrder.display_order")


class CyberpunkOrder(db.Model):
    __tablename__ = "cyberpunk_warehouse_order"
    __table_args__ = (UniqueConstraint("level_id", "display_order", name="uq_cyberpunk_order_order"),)

    id = db.Column(db.Integer, primary_key=True)
    level_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_level.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_customer.id", ondelete="SET NULL"), nullable=True)
    name = db.Column(db.String(120), nullable=False, default="", server_default="")
    display_order = db.Column(db.Integer, nullable=False)
    order_configuration = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    level = db.relationship("CyberpunkLevel", back_populates="orders")
    customer = db.relationship("CyberpunkCustomer", foreign_keys=[customer_id])
    items = db.relationship("CyberpunkOrderItem", back_populates="order", cascade="all, delete-orphan")


class CyberpunkOrderItem(db.Model):
    __tablename__ = "cyberpunk_warehouse_order_item"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity % 3 = 0", name="ck_cyberpunk_order_quantity_multiple_of_three"),
        UniqueConstraint("order_id", "product_id", name="uq_cyberpunk_order_product"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_order.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("cyberpunk_warehouse_product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    order = db.relationship("CyberpunkOrder", back_populates="items")
    product = db.relationship("CyberpunkProduct")

    @validates("quantity")
    def validate_quantity(self, key, value):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value % 3:
            raise ValueError("Order quantities must be positive multiples of three.")
        return value
