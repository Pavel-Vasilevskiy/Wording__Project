import hmac
import hashlib
import logging
import re
import secrets
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
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
from pvp.models import PvPLobby, PvPLobbyKickNotice, PvPLobbyMember
from pvp.presence import (
    HEARTBEAT_INTERVAL_SECONDS,
    cleanup_stale_lobbies,
    is_online,
    mark_member_online,
)


pvp_bp = Blueprint("pvp", __name__, url_prefix="/pvp")
MAX_LOBBY_SIZE = 20
VALID_WORD_COUNTS = {3, 5, 10, 100}
VALID_MODES = {"timer", "no_limit"}
DEFAULT_ATTEMPTS_PER_WORD = 6
MIN_TIMER_SECONDS = 30
MAX_TIMER_SECONDS = 600
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


def _kick_token(public_id, user_id):
    secret = str(current_app.secret_key or "").encode("utf-8")
    message = f"pvp-kick:{public_id}:{user_id}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lobby_snapshot(lobby, user_id, now=None):
    now = now or datetime.utcnow()
    memberships = list(lobby.memberships)
    members = [
        {
            "name": membership.user.name,
            "is_leader": membership.user_id == lobby.leader_id,
            "is_online": is_online(membership, now),
            "is_ready": membership.is_ready and is_online(membership, now),
            "user_id": membership.user_id,
            "membership": membership,
            "kick_token": (
                _kick_token(lobby.public_id, membership.user_id)
                if lobby.leader_id == user_id and membership.user_id != user_id
                else None
            ),
        }
        for membership in memberships
    ]
    all_online_ready = bool(members) and all(item["is_online"] and item["is_ready"] for item in members)
    current_member = next((item for item in members if item["user_id"] == user_id), None)
    offline_count = sum(1 for item in members if not item["is_online"])
    not_ready_count = sum(1 for item in members if item["is_online"] and not item["is_ready"])
    if lobby.status != "open":
        start_explanation = "Лобі вже перейшло до підготовки матчу."
    elif offline_count:
        start_explanation = "Запуск недоступний: дочекайтеся, поки всі учасники будуть онлайн."
    elif not_ready_count:
        start_explanation = "Запуск недоступний: усі учасники, включно з лідером, мають бути готові."
    else:
        start_explanation = "Усі учасники онлайн і готові."
    return {
        "members": members,
        "current_member": current_member,
        "member_count": len(members),
        "online_count": len(members) - offline_count,
        "all_online_ready": all_online_ready,
        "start_explanation": start_explanation,
    }


@pvp_bp.route("/", methods=["GET"])
@login_required
def lobby_list():
    try:
        if request.args.get("kicked") == "1":
            flash("Лідер видалив вас із цього лобі.", "info")
        elif request.args.get("notice") == "unavailable":
            flash("Це лобі більше не існує або ви більше не маєте до нього доступу.", "info")
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


def _lobby_list_payload(user_id):
    my_membership = _my_membership(user_id)
    lobbies = PvPLobby.query.filter_by(status="open").order_by(
        PvPLobby.created_at.asc(), PvPLobby.id.asc()
    ).all()
    return {
        "my_lobby_id": my_membership.lobby_id if my_membership else None,
        "my_lobby_public_id": my_membership.lobby.public_id if my_membership else None,
        "my_lobby_leader": my_membership.lobby.leader.name if my_membership else None,
        "my_lobby_detail_url": (
            url_for("pvp.lobby_detail", public_id=my_membership.lobby.public_id) if my_membership else None
        ),
        "lobbies": [
            {
                "id": lobby.public_id,
                "detail_url": url_for("pvp.lobby_detail", public_id=lobby.public_id),
                "join_url": url_for("pvp.join_lobby", public_id=lobby.public_id),
                "leader": lobby.leader.name,
                "member_count": len(lobby.memberships),
                "max_members": MAX_LOBBY_SIZE,
                "created_at": lobby.created_at.strftime("%d.%m.%Y %H:%M"),
                "can_join": my_membership is None and len(lobby.memberships) < MAX_LOBBY_SIZE,
                "is_mine": bool(my_membership and my_membership.lobby_id == lobby.id),
            }
            for lobby in lobbies
        ],
    }


@pvp_bp.route("/list-state", methods=["POST"])
@login_required
def lobby_list_state():
    """Refresh list and run stale-room cleanup as an authenticated CSRF POST."""
    _check_csrf()
    try:
        _begin_write()
        cleanup_stale_lobbies()
        payload = _lobby_list_payload(current_user.id)
        db.session.commit()
        result = jsonify(payload)
        result.headers["Cache-Control"] = "no-store"
        return result
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not refresh PvP lobby list")
        return {"error": "lobby_list_unavailable"}, 503


@pvp_bp.route("/create", methods=["POST"])
@login_required
def create_lobby():
    _check_csrf()
    user_id = current_user.id
    try:
        _begin_write()
        cleanup_stale_lobbies()
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
        lobby = PvPLobby.query.filter_by(public_id=public_id).first() if _PUBLIC_ID_RE.fullmatch(public_id or "") else None
        if lobby is None:
            flash("Це лобі більше не існує. Можливо, його було закрито", "info")
            return redirect(url_for("pvp.lobby_list"))
        is_member = PvPLobbyMember.query.filter_by(lobby_id=lobby.id, user_id=current_user.id).first()
        if not is_member:
            was_kicked = PvPLobbyKickNotice.query.filter_by(
                public_id=public_id, user_id=current_user.id
            ).first()
            if was_kicked:
                flash("Лідер видалив вас із цього лобі.", "info")
            else:
                flash("Це лобі більше недоступне.", "info")
            return redirect(url_for("pvp.lobby_list"))
        return render_template(
            "pvp_lobby_detail.html",
            lobby=lobby,
            max_lobby_size=MAX_LOBBY_SIZE,
            snapshot=_lobby_snapshot(lobby, current_user.id),
            current_user_id=current_user.id,
            csrf_token=_csrf_token(),
            heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
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
            was_kicked = PvPLobbyKickNotice.query.filter_by(
                public_id=public_id, user_id=current_user.id
            ).first()
            if was_kicked:
                return {"error": "kicked"}, 410
            return {"error": "lobby_unavailable"}, 404

        snapshot = _lobby_snapshot(lobby, current_user.id)
        current_member = snapshot["current_member"]["membership"]
        response = {
            "status": lobby.status,
            "leader": lobby.leader.name,
            "members": [
                {
                    key: item[key]
                    for key in ("name", "is_leader", "is_online", "is_ready", "kick_token")
                    if key != "kick_token" or item[key] is not None
                }
                for item in snapshot["members"]
            ],
            "member_count": snapshot["member_count"],
            "online_count": snapshot["online_count"],
            "max_members": MAX_LOBBY_SIZE,
            "settings": {
                "words_per_match": lobby.words_per_match,
                "mode": lobby.mode,
                "timer_seconds": lobby.timer_seconds,
                "version": lobby.settings_version,
                "attempts_per_word": DEFAULT_ATTEMPTS_PER_WORD,
            },
            "current_user_is_leader": current_member.user_id == lobby.leader_id,
            "current_user_is_ready": snapshot["current_member"]["is_ready"],
            "can_start": lobby.status == "open" and current_member.user_id == lobby.leader_id and snapshot["all_online_ready"],
            "start_explanation": snapshot["start_explanation"],
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        }
        result = jsonify(response)
        result.headers["Cache-Control"] = "no-store"
        return result
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not refresh PvP lobby state")
        return {"error": "lobby_state_unavailable"}, 503


@pvp_bp.route("/<public_id>/heartbeat", methods=["POST"])
@login_required
def lobby_heartbeat(public_id):
    _check_csrf()
    if not _PUBLIC_ID_RE.fullmatch(public_id or ""):
        return {"error": "lobby_unavailable"}, 404
    user_id = current_user.id
    try:
        _begin_write()
        lobby = PvPLobby.query.filter_by(public_id=public_id).first()
        if lobby is None:
            db.session.rollback()
            return {"error": "lobby_unavailable"}, 404
        member = mark_member_online(lobby, user_id)
        if member is None:
            db.session.rollback()
            return {"error": "lobby_unavailable"}, 404
        db.session.commit()
        return jsonify({
            "online": True,
            "ready": member.is_ready,
            "status": lobby.status,
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        })
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not record PvP heartbeat")
        return {"error": "heartbeat_unavailable"}, 503


@pvp_bp.route("/<public_id>/settings", methods=["POST"])
@login_required
def update_lobby_settings(public_id):
    _check_csrf()
    words_per_match = _parse_int(request.form.get("words_per_match"))
    mode = request.form.get("mode", "")
    timer_seconds = _parse_int(request.form.get("timer_seconds"))
    raw_version = request.form.get("settings_version")
    submitted_version = _parse_int(raw_version)
    if raw_version is not None and submitted_version is None:
        if request.accept_mimetypes.best == "application/json":
            return {"error": "invalid_settings_version"}, 400
        flash("Перевірте актуальну версію налаштувань.", "error")
        return redirect(url_for("pvp.lobby_detail", public_id=public_id))
    if words_per_match not in VALID_WORD_COUNTS or mode not in VALID_MODES:
        if request.accept_mimetypes.best == "application/json":
            return {"error": "invalid_settings"}, 400
        flash("Перевірте кількість слів і режим гри.", "error")
        return redirect(url_for("pvp.lobby_detail", public_id=public_id))
    if mode == "timer" and (
        timer_seconds is None or not MIN_TIMER_SECONDS <= timer_seconds <= MAX_TIMER_SECONDS
    ):
        if request.accept_mimetypes.best == "application/json":
            return {"error": "invalid_timer"}, 400
        flash(f"Тривалість таймера має бути від {MIN_TIMER_SECONDS} до {MAX_TIMER_SECONDS} секунд.", "error")
        return redirect(url_for("pvp.lobby_detail", public_id=public_id))

    user_id = current_user.id
    try:
        _begin_write()
        lobby = _lobby_or_404(public_id)
        member = mark_member_online(lobby, user_id)
        if member is None:
            db.session.rollback()
            if request.accept_mimetypes.best == "application/json":
                return {"error": "membership_lost"}, 404
            flash("Ви більше не є учасником цього лобі.", "error")
            return redirect(url_for("pvp.lobby_list"))
        if lobby.leader_id != user_id:
            db.session.commit()
            if request.accept_mimetypes.best == "application/json":
                return {"error": "leader_required"}, 403
            flash("Налаштування може змінювати лише лідер лобі.", "error")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))
        if lobby.status != "open":
            db.session.commit()
            if request.accept_mimetypes.best == "application/json":
                return {"error": "lobby_locked"}, 409
            flash("Налаштування вже зафіксовані для цього стану лобі.", "error")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))

        if submitted_version is not None and submitted_version != lobby.settings_version:
            current_settings = {
                "words_per_match": lobby.words_per_match,
                "mode": lobby.mode,
                "timer_seconds": lobby.timer_seconds,
                "version": lobby.settings_version,
            }
            db.session.commit()
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": "settings_conflict", "settings": current_settings}), 409
            flash("Налаштування вже змінилися в іншій вкладці. Перевірте актуальні значення.", "error")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))

        selected_timer = timer_seconds if mode == "timer" else lobby.timer_seconds
        changed = (
            lobby.words_per_match != words_per_match
            or lobby.mode != mode
            or lobby.timer_seconds != selected_timer
        )
        if changed:
            lobby.words_per_match = words_per_match
            lobby.mode = mode
            lobby.timer_seconds = selected_timer
            for participant in lobby.memberships:
                participant.is_ready = False
            lobby.settings_version += 1
        db.session.commit()
        if request.accept_mimetypes.best == "application/json":
            return jsonify({
                "saved": True,
                "changed": changed,
                "settings": {
                    "words_per_match": lobby.words_per_match,
                    "mode": lobby.mode,
                    "timer_seconds": lobby.timer_seconds,
                    "version": lobby.settings_version,
                },
            })
        flash("Налаштування лобі оновлено." if changed else "Налаштування не змінилися.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not update PvP lobby settings")
        if request.accept_mimetypes.best == "application/json":
            return {"error": "settings_unavailable"}, 503
        flash("Не вдалося зберегти налаштування. Спробуйте ще раз.", "error")
    return redirect(url_for("pvp.lobby_detail", public_id=public_id))


@pvp_bp.route("/<public_id>/ready", methods=["POST"])
@login_required
def update_ready_status(public_id):
    _check_csrf()
    requested_ready = request.form.get("ready")
    if requested_ready not in {"true", "false"}:
        flash("Некоректний статус готовності.", "error")
        return redirect(url_for("pvp.lobby_detail", public_id=public_id))

    user_id = current_user.id
    try:
        _begin_write()
        lobby = _lobby_or_404(public_id)
        member = mark_member_online(lobby, user_id)
        if member is None:
            db.session.rollback()
            flash("Ви більше не є учасником цього лобі.", "error")
            return redirect(url_for("pvp.lobby_list"))
        if lobby.status != "open":
            db.session.commit()
            flash("Змінити готовність після підготовки матчу вже не можна.", "error")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))

        member.is_ready = requested_ready == "true"
        db.session.commit()
        flash("Статус готовності оновлено.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not update PvP ready status")
        flash("Не вдалося оновити готовність. Спробуйте ще раз.", "error")
    return redirect(url_for("pvp.lobby_detail", public_id=public_id))


@pvp_bp.route("/<public_id>/start", methods=["POST"])
@login_required
def start_lobby(public_id):
    _check_csrf()
    user_id = current_user.id
    try:
        _begin_write()
        lobby = _lobby_or_404(public_id)
        member = mark_member_online(lobby, user_id)
        if member is None:
            db.session.rollback()
            flash("Ви більше не є учасником цього лобі.", "error")
            return redirect(url_for("pvp.lobby_list"))
        if lobby.leader_id != user_id:
            db.session.commit()
            flash("Запустити підготовку може лише лідер лобі.", "error")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))
        if lobby.status != "open":
            db.session.commit()
            flash("Це лобі вже перейшло до підготовки матчу.", "info")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))

        members = list(lobby.memberships)
        now = datetime.utcnow()
        if any(not is_online(participant, now) for participant in members):
            db.session.commit()
            flash("Запуск недоступний: дочекайтеся, поки всі учасники будуть онлайн.", "error")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))
        if any(not participant.is_ready for participant in members):
            db.session.commit()
            flash("Запуск недоступний: усі учасники, включно з лідером, мають бути готові.", "error")
            return redirect(url_for("pvp.lobby_detail", public_id=public_id))

        lobby.status = "starting"
        db.session.commit()
        flash("Усі готові. Лобі перейшло до підготовки матчу.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not start PvP lobby")
        flash("Не вдалося змінити стан лобі. Спробуйте ще раз.", "error")
    return redirect(url_for("pvp.lobby_detail", public_id=public_id))


@pvp_bp.route("/<public_id>/kick", methods=["POST"])
@login_required
def kick_lobby_member(public_id):
    _check_csrf()
    token = request.form.get("member_token", "")
    if not _PUBLIC_ID_RE.fullmatch(public_id or "") or not re.fullmatch(r"[0-9a-f]{64}", token):
        return {"error": "member_unavailable"}, 404
    try:
        _begin_write()
        lobby = PvPLobby.query.filter_by(public_id=public_id).first()
        if lobby is None:
            db.session.rollback()
            return {"error": "lobby_unavailable"}, 404
        actor = mark_member_online(lobby, current_user.id)
        if actor is None:
            db.session.rollback()
            return {"error": "leader_required"}, 403
        if lobby.leader_id != current_user.id:
            db.session.commit()
            return {"error": "leader_required"}, 403
        if lobby.status == "in_progress":
            db.session.rollback()
            return {"error": "match_in_progress"}, 409

        target = next((
            member for member in lobby.memberships
            if member.user_id != current_user.id
            and hmac.compare_digest(_kick_token(public_id, member.user_id), token)
        ), None)
        if target is None:
            db.session.commit()
            # Idempotent after a prior removal; do not disclose membership details.
            return jsonify({"removed": False})

        notice = PvPLobbyKickNotice.query.filter_by(
            public_id=public_id, user_id=target.user_id
        ).first()
        if notice is None:
            db.session.add(PvPLobbyKickNotice(public_id=public_id, user_id=target.user_id))
        db.session.delete(target)
        db.session.commit()
        return jsonify({"removed": True})
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not remove PvP lobby member")
        return {"error": "kick_unavailable"}, 503


@pvp_bp.route("/<public_id>/join", methods=["POST"])
@login_required
def join_lobby(public_id):
    _check_csrf()
    user_id = current_user.id
    try:
        _begin_write()
        cleanup_stale_lobbies()
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
        old_notice = PvPLobbyKickNotice.query.filter_by(public_id=lobby.public_id, user_id=user_id).first()
        if old_notice:
            db.session.delete(old_notice)
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
