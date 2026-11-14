"""Cyberpunk Warehouse game foundation."""

from cyberpunk_warehouse.models import (  # noqa: F401
    CyberpunkAsset,
    CyberpunkCustomer,
    CyberpunkGameMode,
    CyberpunkGameSession,
    CyberpunkGameAction,
    CyberpunkLevel,
    CyberpunkOrder,
    CyberpunkOrderItem,
    CyberpunkProduct,
)
from cyberpunk_warehouse.routes import cyberpunk_warehouse_admin_bp
from cyberpunk_warehouse.player_routes import cyberpunk_warehouse_game_bp
from cyberpunk_warehouse.services.modes import seed_builtin_game_modes


def register_blueprints(app):
    """Register the player game and administration surfaces."""
    app.register_blueprint(cyberpunk_warehouse_game_bp)
    app.register_blueprint(cyberpunk_warehouse_admin_bp)


__all__ = ["register_blueprints", "seed_builtin_game_modes"]
