"""Player-facing routes for the Cyberpunk Warehouse Classic engine."""
from datetime import datetime
import secrets

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from cyberpunk_warehouse.models import (
    CyberpunkAsset, CyberpunkGameAction, CyberpunkGameSession, CyberpunkLevel,
    CyberpunkProduct,
)
from cyberpunk_warehouse.services.assets import asset_storage_directory, validate_storage_reference
from cyberpunk_warehouse.services.game_engine import GameActionError, get_game_engine
from cyberpunk_warehouse.services.levels import level_publish_errors, normalize_warehouse_layout
from models import db


cyberpunk_warehouse_game_bp = Blueprint(
    "cyberpunk_warehouse_game", __name__, url_prefix="/cyberpunk-warehouse"
)


def _game_csrf_token():
    token = session.get("cyberpunk_game_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["cyberpunk_game_csrf"] = token
    return token


def _check_game_csrf():
    expected = session.get("cyberpunk_game_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "") if request.is_json else request.form.get("csrf_token", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        abort(400, description="Invalid game security token.")


def _image_url(asset):
    return url_for("cyberpunk_warehouse_game.game_asset", asset_id=asset.id) if asset else None


def _level_snapshot(level):
    layout = normalize_warehouse_layout(level.warehouse_layout)
    product_ids = {
        int(slot)
        for shelf in layout["shelves"] for cell in shelf["cells"] for slot in cell["slots"]
        if slot is not None
    }
    for order in level.orders:
        product_ids.update(item.product_id for item in order.items)
    products = CyberpunkProduct.query.filter(CyberpunkProduct.id.in_(product_ids)).all() if product_ids else []
    product_map = {
        str(product.id): {
            "id": product.id, "name": product.name,
            "image_url": _image_url(product.image_asset) if product.image_asset and product.image_asset.is_active else None,
            "alt_text": product.image_asset.alt_text if product.image_asset else product.name,
        }
        for product in products
    }
    orders = []
    for order in level.orders:
        customer = order.customer or level.customer
        orders.append({
            "name": order.name or f"Order {order.display_order}",
            "customer": {
                "name": customer.name,
                "waiting_seconds": customer.default_waiting_seconds,
                "image_url": _image_url(customer.character_artwork) if customer.character_artwork else None,
            } if customer else None,
            "items": [{"product_id": item.product_id, "quantity": item.quantity} for item in order.items],
        })
    return {
        "id": level.id, "name": level.name, "level_number": level.level_number,
        "mode": level.game_mode.key, "mode_name": level.game_mode.display_name,
        "warehouse_layout": layout,
        "gameplay_configuration": dict(level.gameplay_configuration or {}),
        "mode_configuration": dict(level.mode_configuration or {}),
        "time_limit_seconds": level.time_limit_seconds,
        "products": product_map, "orders": orders,
        "background_url": _image_url(level.background_asset) if level.background_asset else None,
        "shelf_url": _image_url(level.shelf_asset) if level.shelf_asset else None,
    }


def _state_response(game_session):
    return {
        "session_id": game_session.id,
        "revision": game_session.revision,
        "status": game_session.status,
        "state": game_session.state,
    }


@cyberpunk_warehouse_game_bp.get("/")
@login_required
def level_list():
    levels = CyberpunkLevel.query.join(CyberpunkLevel.game_mode).filter(
        CyberpunkLevel.status == "published", CyberpunkLevel.game_mode.has(key="classic")
    ).order_by(CyberpunkLevel.sort_order, CyberpunkLevel.level_number, CyberpunkLevel.id).all()
    available = [level for level in levels if not level_publish_errors(level)]
    sessions = CyberpunkGameSession.query.filter(
        CyberpunkGameSession.user_id == current_user.id,
        CyberpunkGameSession.status.in_(["active", "paused"]),
    ).order_by(CyberpunkGameSession.updated_at.desc()).all()
    csrf_token = _game_csrf_token()
    return render_template(
        "cyberpunk_warehouse/levels.html", levels=available, sessions=sessions,
        csrf_token=csrf_token,
    )


@cyberpunk_warehouse_game_bp.post("/levels/<int:level_id>/start")
@login_required
def start_level(level_id):
    _check_game_csrf()
    level = db.session.get(CyberpunkLevel, level_id)
    if level is None or level.status != "published":
        abort(404)
    if level.game_mode is None or level.game_mode.key != "classic":
        flash("This game mode is not available yet.", "error")
        return redirect(url_for("cyberpunk_warehouse_game.level_list"))
    errors = level_publish_errors(level)
    if errors:
        flash("This level is not currently playable.", "error")
        current_app.logger.warning("Published Cyberpunk level %s failed runtime validation: %s", level.id, errors)
        return redirect(url_for("cyberpunk_warehouse_game.level_list"))
    snapshot = _level_snapshot(level)
    engine = get_game_engine(level.game_mode.key)
    try:
        state = engine.initial_state(snapshot)
        game_session = CyberpunkGameSession(user_id=current_user.id, level_id=level.id, status="active", state=state)
        db.session.add(game_session)
        db.session.commit()
    except (GameActionError, IntegrityError):
        db.session.rollback()
        current_app.logger.exception("Could not create Cyberpunk Warehouse game session")
        flash("Could not start this level. Please try again.", "error")
        return redirect(url_for("cyberpunk_warehouse_game.level_list"))
    return redirect(url_for("cyberpunk_warehouse_game.play_session", session_id=game_session.id))


@cyberpunk_warehouse_game_bp.get("/session/<int:session_id>")
@login_required
def play_session(session_id):
    game_session = CyberpunkGameSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    csrf_token = _game_csrf_token()
    return render_template(
        "cyberpunk_warehouse/game.html", game_session=game_session,
        csrf_token=csrf_token,
    )


@cyberpunk_warehouse_game_bp.post("/api/session/<int:session_id>/action")
@login_required
def game_action(session_id):
    _check_game_csrf()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Invalid action request."), 400
    action_id = body.get("action_id")
    if not isinstance(action_id, str) or not (8 <= len(action_id) <= 64):
        return jsonify(error="A valid action identifier is required."), 400
    game_session = CyberpunkGameSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    existing_action = CyberpunkGameAction.query.filter_by(game_session_id=session_id, action_id=action_id).first()
    if existing_action:
        return jsonify(existing_action.result)
    expected_revision = body.get("revision")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        return jsonify(error="A valid state revision is required.", current=_state_response(game_session)), 400
    if expected_revision != game_session.revision:
        return jsonify(error="The game state has changed. The latest state was loaded.", current=_state_response(game_session)), 409

    engine = get_game_engine(game_session.state.get("level", {}).get("mode"))
    if engine is None:
        return jsonify(error="This game mode is unavailable."), 409
    try:
        next_state = engine.apply(game_session.state, body.get("action"), now=datetime.utcnow())
    except GameActionError as exc:
        return jsonify(error=str(exc), current=_state_response(game_session)), 400

    next_revision = game_session.revision + 1
    response_data = {
        "session_id": game_session.id, "revision": next_revision,
        "status": next_state["status"], "state": next_state,
    }
    try:
        changed = CyberpunkGameSession.query.filter_by(
            id=session_id, user_id=current_user.id, revision=expected_revision,
        ).update({
            CyberpunkGameSession.state: next_state,
            CyberpunkGameSession.status: next_state["status"],
            CyberpunkGameSession.revision: next_revision,
            CyberpunkGameSession.updated_at: datetime.utcnow(),
            CyberpunkGameSession.completed_at: datetime.utcnow() if next_state["status"] == "completed" else None,
        }, synchronize_session=False)
        if not changed:
            db.session.rollback()
            replay = CyberpunkGameAction.query.filter_by(game_session_id=session_id, action_id=action_id).first()
            if replay:
                return jsonify(replay.result)
            latest = CyberpunkGameSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
            return jsonify(error="The game state has changed. The latest state was loaded.", current=_state_response(latest)), 409
        db.session.add(CyberpunkGameAction(game_session_id=session_id, action_id=action_id, result=response_data))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        replay = CyberpunkGameAction.query.filter_by(game_session_id=session_id, action_id=action_id).first()
        if replay:
            return jsonify(replay.result)
        return jsonify(error="The action could not be saved. Please retry."), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not persist Cyberpunk Warehouse action")
        return jsonify(error="The action could not be saved. Please retry."), 500
    return jsonify(response_data)


@cyberpunk_warehouse_game_bp.get("/assets/<int:asset_id>")
def game_asset(asset_id):
    """Serve only active artwork referenced by game catalog or levels."""
    asset = db.session.get(CyberpunkAsset, asset_id)
    if asset is None or not asset.is_active:
        abort(404)
    referenced = (
        CyberpunkProduct.query.filter_by(image_asset_id=asset.id, is_active=True).first()
        or CyberpunkLevel.query.filter(
            CyberpunkLevel.status == "published",
            (CyberpunkLevel.background_asset_id == asset.id) | (CyberpunkLevel.shelf_asset_id == asset.id),
        ).first()
    )
    if not referenced and asset.asset_type not in {"customer", "customer_emotion"}:
        abort(404)
    if asset.asset_type in {"customer", "customer_emotion"}:
        from cyberpunk_warehouse.models import CyberpunkCustomer
        customer = CyberpunkCustomer.query.filter(
            CyberpunkCustomer.is_active.is_(True),
            (CyberpunkCustomer.character_artwork_id == asset.id)
            | (CyberpunkCustomer.happy_artwork_id == asset.id)
            | (CyberpunkCustomer.impatient_artwork_id == asset.id)
            | (CyberpunkCustomer.disappointed_artwork_id == asset.id),
        ).first()
        if not customer:
            abort(404)
    try:
        relative = validate_storage_reference(asset.storage_path)
        root = asset_storage_directory(current_app.config)
        target = (root / "/".join(relative.parts)).resolve()
        if target.parent != root or not target.is_file():
            abort(404)
        response = send_file(target, mimetype=asset.media_type, conditional=True, max_age=3600)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except (RuntimeError, OSError, ValueError):
        abort(404)
