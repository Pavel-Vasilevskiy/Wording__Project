import hmac
import logging
import re
import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import db
from pvp.models import PvPLobby, PvPLobbyMember


pvp_bp = Blueprint("pvp", __name__, url_prefix="/pvp")
MAX_LOBBY_SIZE = 20
_PUBLIC_ID_RE = re.compile(r"^[0-9a-f]{32}$")
logger = logging.getLogger(__name__)


def _csrf_token():
    token = session.get("pvp_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["pvp_csrf_token"] = token
    return token


def _check_csrf():
    supplied = request.form.get("csrf_token", "")
    expected = session.get("pvp_csrf_token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        abort(400, description="The form expired. Reload the page and try again.")


def _begin_write():
    """Serialize lobby mutations on SQLite before checking capacity or membership."""
    # Login's user loader may already have opened a read transaction.
    session_db = db.session()
    if session_db.in_transaction():
        session_db.rollback()
    session_db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _lobby_or_404(public_id):
    if not _PUBLIC_ID_RE.fullmatch(public_id or ""):
        abort(404)
    lobby = PvPLobby.query.filter_by(public_id=public_id).first()
    if lobby is None:
        abort(404)
    return lobby


def _my_membership(user_id):
    return PvPLobbyMember.query.filter_by(user_id=user_id).first()


@pvp_bp.route("/", methods=["GET"])
@login_required
def lobby_list():
    try:
        my_membership = _my_membership(current_user.id)
        lobbies = PvPLobby.query.filter_by(status="open").order_by(
            PvPLobby.created_at.asc(), PvPLobby.id.asc()
        ).all()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not load PvP lobbies")
        my_membership = None
        lobbies = []
        flash("Не вдалося завантажити лобі. Спробуйте пізніше.", "error")
    return render_template(
        "pvp_lobbies.html",
        lobbies=lobbies,
        my_membership=my_membership,
        max_lobby_size=MAX_LOBBY_SIZE,
        csrf_token=_csrf_token(),
    )


@pvp_bp.route("/create", methods=["POST"])
@login_required
def create_lobby():
    _check_csrf()
    user_id = current_user.id
    try:
        _begin_write()
        existing = _my_membership(user_id)
        if existing:
            public_id = existing.lobby.public_id
            db.session.commit()
            flash("Ви вже перебуваєте в лобі.", "info")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))

        lobby = PvPLobby(leader_id=user_id, status="open")
        db.session.add(lobby)
        db.session.flush()
        db.session.add(PvPLobbyMember(lobby_id=lobby.id, user_id=user_id))
        public_id = lobby.public_id
        db.session.commit()
        flash("Лобі створено.", "success")
        return redirect(url_for("pvp.lobby_detail", public_id=public_id))
    except IntegrityError:
        db.session.rollback()
        # A concurrent create can only leave this user in one lobby due to the DB constraint.
        existing = _my_membership(user_id)
        if existing:
            flash("Ви вже перебуваєте в лобі.", "info")
            return redirect(url_for("pvp.lobby_detail", public_id=existing.lobby.public_id))
        flash("Не вдалося створити лобі. Спробуйте ще раз.", "error")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not create PvP lobby")
        flash("Сервіс лобі тимчасово недоступний.", "error")
    return redirect(url_for("pvp.lobby_list"))


@pvp_bp.route("/<public_id>", methods=["GET"])
@login_required
def lobby_detail(public_id):
    try:
        lobby = _lobby_or_404(public_id)
        is_member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=current_user.id).first()
        if not is_member:
            abort(404)
        return render_template(
            "pvp_lobby_detail.html",
            lobby=lobby,
            max_lobby_size=MAX_LOBBY_SIZE,
            csrf_token=_csrf_token(),
        )
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not load PvP lobby details")
        abort(503)


@pvp_bp.route("/<public_id>/state", methods=["GET"])
@login_required
def lobby_state(public_id):
    """Return only the member and lobby information needed by the lobby page."""
    if not _PUBLIC_ID_RE.fullmatch(public_id or ""):
        return {"error": "lobby_unavailable"}, 404
    try:
        lobby = PvPLobby.query.filter_by(public_id=public_id).first()
        if lobby is None:
            return {"error": "lobby_unavailable"}, 404
        is_member = PvPLobbyMember.query.filter_by(
            lobby_id=lobby.id, user_id=current_user.id
        ).first()
        if not is_member:
            return {"error": "lobby_unavailable"}, 404

        members = [
            {
                "name": member.user.name,
                "is_leader": member.user_id == lobby.leader_id,
            }
            for member in lobby.memberships
        ]
        response = {
            "status": lobby.status,
            "leader": lobby.leader.name,
            "members": members,
            "member_count": len(members),
            "max_members": MAX_LOBBY_SIZE,
        }
        result = jsonify(response)
        result.headers["Cache-Control"] = "no-store"
        return result
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not refresh PvP lobby state")
        return {"error": "lobby_state_unavailable"}, 503


@pvp_bp.route("/<public_id>/join", methods=["POST"])
@login_required
def join_lobby(public_id):
    _check_csrf()
    user_id = current_user.id
    try:
        _begin_write()
        lobby = _lobby_or_404(public_id)
        membership = _my_membership(user_id)
        if membership:
            if membership.lobby_id == lobby.id:
                db.session.commit()
                return redirect(url_for("pvp.lobby_detail", public_id=lobby.public_id))
            db.session.rollback()
            flash("Спочатку залиште поточне лобі.", "error")
            return redirect(url_for("pvp.lobby_list"))
        if lobby.status != "open":
            db.session.rollback()
            flash("Це лобі вже закрите.", "error")
            return redirect(url_for("pvp.lobby_list"))
        member_count = PvPLobbyMember.query.filter_by(lobby_id=lobby.id).count()
        if member_count >= MAX_LOBBY_SIZE:
            db.session.rollback()
            flash("У цьому лобі вже немає вільних місць.", "error")
            return redirect(url_for("pvp.lobby_list"))

        db.session.add(PvPLobbyMember(lobby_id=lobby.id, user_id=user_id))
        db.session.commit()
        flash("Ви приєдналися до лобі.", "success")
        return redirect(url_for("pvp.lobby_detail", public_id=lobby.public_id))
    except IntegrityError:
        db.session.rollback()
        flash("Не вдалося приєднатися: лобі змінилося або ви вже в іншому лобі.", "error")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not join PvP lobby")
        flash("Сервіс лобі тимчасово недоступний.", "error")
    return redirect(url_for("pvp.lobby_list"))


@pvp_bp.route("/<public_id>/leave", methods=["POST"])
@login_required
def leave_lobby(public_id):
    _check_csrf()
    user_id = current_user.id
    try:
        _begin_write()
        if not _PUBLIC_ID_RE.fullmatch(public_id or ""):
            db.session.rollback()
            flash("Лобі вже недоступне.", "info")
            return redirect(url_for("pvp.lobby_list"))

        lobby = PvPLobby.query.filter_by(public_id=public_id).first()
        if lobby is None:
            db.session.rollback()
            flash("Лобі вже недоступне.", "info")
            return redirect(url_for("pvp.lobby_list"))

        membership = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=user_id).first()
        if not membership:
            db.session.rollback()
            flash("Ви вже залишили це лобі.", "info")
            return redirect(url_for("pvp.lobby_list"))

        db.session.delete(membership)
        remaining = PvPLobbyMember.query.filter_by(lobby_id=lobby.id).order_by(
            PvPLobbyMember.joined_at.asc(), PvPLobbyMember.user_id.asc()
        ).all()
        if not remaining:
            # Empty rooms are deleted immediately; there is no lobby history in this iteration.
            db.session.delete(lobby)
        elif lobby.leader_id == user_id:
            lobby.leader_id = remaining[0].user_id
        db.session.commit()
        flash("Ви залишили лобі.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not leave PvP lobby")
        flash("Не вдалося залишити лобі. Спробуйте ще раз.", "error")
        return redirect(url_for("pvp.lobby_detail", public_id=public_id))
    return redirect(url_for("pvp.lobby_list"))
