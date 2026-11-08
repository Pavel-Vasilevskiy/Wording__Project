from functools import wraps

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required

from cyberpunk_warehouse.models import (
    CyberpunkAsset,
    CyberpunkCustomer,
    CyberpunkGameMode,
    CyberpunkLevel,
    CyberpunkOrder,
    CyberpunkProduct,
)


cyberpunk_warehouse_admin_bp = Blueprint(
    "cyberpunk_warehouse_admin",
    __name__,
    url_prefix="/admin/cyberpunk-warehouse",
)

PLANNED_SECTIONS = {
    "products": ("Products", "Catalog management and artwork assignment are planned for the next administration iteration."),
    "customers": ("Customers", "Customer profiles, character art, and emotion states are planned."),
    "orders": ("Orders", "Order creation and product quantity editing are planned."),
    "settings": ("Game Settings", "Global gameplay tuning is planned after level configuration settles."),
    "assets": ("Assets", "Safe artwork upload and replacement are planned. No artwork is bundled yet."),
    "analytics": ("Analytics", "Usage and performance reporting is planned after gameplay data exists."),
}


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@cyberpunk_warehouse_admin_bp.route("/")
@login_required
@_admin_required
def dashboard():
    totals = {
        "levels": CyberpunkLevel.query.count(),
        "products": CyberpunkProduct.query.count(),
        "customers": CyberpunkCustomer.query.count(),
        "orders": CyberpunkOrder.query.count(),
        "assets": CyberpunkAsset.query.count(),
        "modes": CyberpunkGameMode.query.count(),
    }
    sections = [
        {"key": "levels", "title": "Levels", "state": "Foundation", "url": "/admin/cyberpunk-warehouse/levels", "available": True},
        {"key": "game-modes", "title": "Game Modes", "state": "Foundation", "url": "/admin/cyberpunk-warehouse/game-modes", "available": True},
    ]
    sections.extend(
        {"key": key, "title": title, "state": "Planned", "url": f"/admin/cyberpunk-warehouse/sections/{key}", "available": False}
        for key, (title, _description) in PLANNED_SECTIONS.items()
    )
    return render_template("cyberpunk_warehouse_admin/dashboard.html", totals=totals, sections=sections)


@cyberpunk_warehouse_admin_bp.route("/levels")
@login_required
@_admin_required
def levels():
    levels = CyberpunkLevel.query.order_by(CyberpunkLevel.level_number, CyberpunkLevel.id).limit(100).all()
    return render_template("cyberpunk_warehouse_admin/levels.html", levels=levels)


@cyberpunk_warehouse_admin_bp.route("/game-modes")
@login_required
@_admin_required
def game_modes():
    modes = CyberpunkGameMode.query.order_by(CyberpunkGameMode.id).all()
    return render_template("cyberpunk_warehouse_admin/game_modes.html", modes=modes)


@cyberpunk_warehouse_admin_bp.route("/sections/<section_key>")
@login_required
@_admin_required
def planned_section(section_key):
    section = PLANNED_SECTIONS.get(section_key)
    if section is None:
        abort(404)
    return render_template(
        "cyberpunk_warehouse_admin/planned.html",
        section_title=section[0],
        section_description=section[1],
    )
