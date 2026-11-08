import re
import secrets
from datetime import datetime

import click
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import MetaData, Table, func, inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import User, db
from connections.models import ConnectionsCategory, ConnectionsPuzzle, ConnectionsWord


connections_admin_bp = Blueprint(
    "connections_admin", __name__, url_prefix="/admin/connections"
)

PUZZLE_STATUSES = {"draft", "review", "approved", "archived"}
DIFFICULTIES = {"yellow", "green", "blue", "purple"}
LANGUAGE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
PAGE_SIZE = 20


def admin_required(view):
    """Require an authenticated account explicitly granted admin privileges."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _csrf_token():
    token = session.get("connections_admin_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["connections_admin_csrf"] = token
    return token


def _require_csrf(payload=None):
    expected = session.get("connections_admin_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied and isinstance(payload, dict):
        supplied = payload.get("csrf_token", "")
    if not supplied:
        supplied = request.form.get("csrf_token", "")
    if not expected or not isinstance(supplied, str) or not secrets.compare_digest(expected, supplied):
        abort(400, description="Invalid form security token.")


def _empty_form():
    return {
        "title": "",
        "language": "en",
        "status": "draft",
        "categories": [
            {"id": None, "name": "", "difficulty": difficulty, "description": "", "words": ["", "", "", ""]}
            for difficulty in ("yellow", "green", "blue", "purple")
        ],
    }


def _puzzle_data(puzzle):
    categories = []
    for category in puzzle.categories:
        words = [word.text for word in category.words]
        categories.append({
            "id": category.id,
            "name": category.name,
            "difficulty": category.difficulty,
            "description": category.description,
            "words": words + [""] * (4 - len(words)),
        })
    while len(categories) < 4:
        categories.append(_empty_form()["categories"][len(categories)])
    return {
        "id": puzzle.id,
        "title": puzzle.title,
        "language": puzzle.language,
        "status": puzzle.status,
        "categories": categories,
    }


def _payload_from_form():
    categories = []
    for index in range(4):
        categories.append({
            "id": request.form.get(f"category_{index}_id") or None,
            "name": request.form.get(f"category_{index}_name", ""),
            "difficulty": request.form.get(f"category_{index}_difficulty", "yellow"),
            "description": request.form.get(f"category_{index}_description", ""),
            "words": [request.form.get(f"category_{index}_word_{word_index}", "") for word_index in range(4)],
        })
    return {
        "title": request.form.get("title", ""),
        "language": request.form.get("language", "en"),
        "status": request.form.get("status", "draft"),
        "categories": categories,
    }


def _normalize_payload(payload, puzzle=None):
    if not isinstance(payload, dict):
        raise ValueError("Puzzle data must be an object.")
    title = payload.get("title", "")
    language = payload.get("language", "en")
    status = payload.get("status", "draft")
    if not isinstance(title, str) or not isinstance(language, str) or not isinstance(status, str):
        raise ValueError("Title, language, and status must be text values.")
    title = title.strip()
    language = language.strip()
    status = status.strip().lower()
    if not title:
        raise ValueError("Puzzle title is required.")
    if len(title) > 120:
        raise ValueError("Puzzle title must be 120 characters or fewer.")
    if not LANGUAGE_RE.fullmatch(language):
        raise ValueError("Language must use a supported language code such as en or en-US.")
    if status not in PUZZLE_STATUSES:
        raise ValueError("Invalid puzzle status.")

    raw_categories = payload.get("categories", [])
    if not isinstance(raw_categories, list) or len(raw_categories) > 4:
        raise ValueError("A puzzle can contain up to four categories.")
    if puzzle is not None:
        existing_by_id = {category.id: category for category in puzzle.categories}
    else:
        existing_by_id = {}

    categories = []
    seen_ids = set()
    for index in range(4):
        raw = raw_categories[index] if index < len(raw_categories) else {}
        if not isinstance(raw, dict):
            raise ValueError("Each category must be an object.")
        raw_id = raw.get("id")
        if raw_id not in (None, ""):
            try:
                category_id = int(raw_id)
            except (TypeError, ValueError):
                raise ValueError("Invalid category identifier.") from None
            if category_id not in existing_by_id or category_id in seen_ids:
                raise ValueError("Category does not belong to this puzzle.")
            if existing_by_id[category_id].display_order != index:
                raise ValueError("Category identifiers must match their puzzle positions.")
            seen_ids.add(category_id)
        else:
            category_id = None

        name = raw.get("name", "")
        difficulty = raw.get("difficulty", "yellow")
        description = raw.get("description", "")
        words = raw.get("words", [])
        if not isinstance(name, str) or not isinstance(difficulty, str) or not isinstance(description, str):
            raise ValueError("Category name, difficulty, and description must be text values.")
        name, difficulty, description = name.strip(), difficulty.strip().lower(), description.strip()
        if len(name) > 100 or len(description) > 500:
            raise ValueError("Category name or description is too long.")
        if difficulty not in DIFFICULTIES:
            raise ValueError("Invalid category difficulty.")
        if not isinstance(words, list) or len(words) > 4:
            raise ValueError("Each category can contain up to four words.")
        normalized_words = []
        for word in words:
            if not isinstance(word, str):
                raise ValueError("Words must be text values.")
            word = word.strip()
            if len(word) > 60:
                raise ValueError("Words must be 60 characters or fewer.")
            normalized_words.append(word)
        normalized_words.extend([""] * (4 - len(normalized_words)))
        categories.append({
            "id": category_id,
            "name": name,
            "difficulty": difficulty,
            "description": description,
            "words": normalized_words,
        })

    if status in {"review", "approved"}:
        if any(not category["name"] for category in categories):
            raise ValueError("All four category names are required before review or approval.")
        words = [word for category in categories for word in category["words"]]
        if any(not word for word in words):
            raise ValueError("All 16 words are required before review or approval.")
        normalized = [word.casefold() for word in words]
        if len(set(normalized)) != 16:
            raise ValueError("Words must be unique within a puzzle.")

    return {"title": title, "language": language, "status": status, "categories": categories}


def _save_puzzle(payload, puzzle=None):
    data = _normalize_payload(payload, puzzle=puzzle)
    if puzzle is None:
        puzzle = ConnectionsPuzzle(created_by=current_user.id)
        db.session.add(puzzle)
    puzzle.title = data["title"]
    puzzle.language = data["language"]
    puzzle.status = data["status"]
    puzzle.updated_at = datetime.utcnow()

    current_categories = {category.id: category for category in puzzle.categories if category.id is not None}
    ordered_categories = sorted(puzzle.categories, key=lambda item: item.display_order)
    for index, category_data in enumerate(data["categories"]):
        category = current_categories.get(category_data["id"]) if category_data["id"] else None
        if category is None and index < len(ordered_categories):
            category = ordered_categories[index]
        if category is None:
            category = ConnectionsCategory(display_order=index)
            puzzle.categories.append(category)
        category.name = category_data["name"]
        category.difficulty = category_data["difficulty"]
        category.description = category_data["description"]
        category.display_order = index

        existing_words = sorted(category.words, key=lambda item: item.display_order)
        for word_index, value in enumerate(category_data["words"]):
            word = existing_words[word_index] if word_index < len(existing_words) else ConnectionsWord()
            word.text = value
            word.display_order = word_index
            if word_index >= len(existing_words):
                category.words.append(word)
        for extra_word in existing_words[4:]:
            category.words.remove(extra_word)

    # Remove any legacy extra categories, keeping their deletion cascading to words.
    for category in list(puzzle.categories):
        if category.display_order >= 4:
            puzzle.categories.remove(category)
    return puzzle


def _serialize_puzzle(puzzle):
    data = _puzzle_data(puzzle)
    data["created_at"] = puzzle.created_at.isoformat() if puzzle.created_at else None
    data["updated_at"] = puzzle.updated_at.isoformat() if puzzle.updated_at else None
    data["created_by"] = puzzle.created_by
    return data


def _puzzle_is_referenced(puzzle_id):
    """Honor future FK references as a safety guard before deleting a puzzle."""
    inspector = inspect(db.engine)
    metadata = MetaData()
    for table_name in inspector.get_table_names():
        if table_name in {"connections_category", "connections_word"}:
            continue
        for foreign_key in inspector.get_foreign_keys(table_name):
            if foreign_key.get("referred_table") != ConnectionsPuzzle.__tablename__:
                continue
            columns = foreign_key.get("constrained_columns") or []
            if not columns:
                continue
            table = Table(table_name, metadata, autoload_with=db.engine)
            column = table.c.get(columns[0])
            if column is not None and db.session.execute(select(table.c[columns[0]]).where(column == puzzle_id).limit(1)).first():
                return True
    return False


def _apply_form_data(puzzle, payload):
    try:
        puzzle = _save_puzzle(payload, puzzle=puzzle)
        db.session.commit()
        return None, puzzle
    except ValueError as exc:
        db.session.rollback()
        return str(exc), None
    except IntegrityError:
        db.session.rollback()
        current_app.logger.warning("Connections puzzle save rejected by a database constraint")
        return "The puzzle could not be saved because its data conflicts with a database constraint.", None
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Connections puzzle could not be saved")
        return "The puzzle could not be saved. Please try again.", None


@connections_admin_bp.route("/")
@connections_admin_bp.route("/puzzles")
@login_required
@admin_required
def library():
    search = request.args.get("q", "").strip()[:100]
    status = request.args.get("status", "").strip().lower()
    language = request.args.get("language", "").strip()[:16]
    sort = request.args.get("sort", "created")
    direction = request.args.get("direction", "desc").lower()
    sort_columns = {
        "id": ConnectionsPuzzle.id,
        "title": ConnectionsPuzzle.title,
        "language": ConnectionsPuzzle.language,
        "status": ConnectionsPuzzle.status,
        "created": ConnectionsPuzzle.created_at,
        "updated": ConnectionsPuzzle.updated_at,
    }
    if sort not in sort_columns:
        sort = "created"
    page_arg = request.args.get("page", "1")
    try:
        page = max(1, int(page_arg))
    except (TypeError, ValueError):
        page = 1
    counts = {
        key: db.session.query(ConnectionsPuzzle.id).filter_by(status=key).count()
        for key in PUZZLE_STATUSES
    }
    query = ConnectionsPuzzle.query
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(ConnectionsPuzzle.title.ilike(f"%{escaped}%", escape="\\"))
    if status in PUZZLE_STATUSES:
        query = query.filter_by(status=status)
    if language:
        query = query.filter_by(language=language)
    order_column = sort_columns[sort]
    query = query.order_by(order_column.desc() if direction == "desc" else order_column.asc(), ConnectionsPuzzle.id.desc())
    pagination = query.paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    languages = [row[0] for row in db.session.query(ConnectionsPuzzle.language).distinct().order_by(ConnectionsPuzzle.language).all()]
    return render_template(
        "connections_admin/library.html",
        puzzles=pagination.items,
        pagination=pagination,
        counts=counts,
        search=search,
        status=status if status in PUZZLE_STATUSES else "",
        language=language,
        languages=languages,
        sort=sort,
        direction=direction,
        csrf_token=_csrf_token(),
    )


@connections_admin_bp.route("/puzzles/new", methods=["GET", "POST"])
@login_required
@admin_required
def create_puzzle():
    form_data = _empty_form()
    if request.method == "POST":
        payload = _payload_from_form()
        _require_csrf(payload)
        form_data.update(payload)
        message, puzzle = _apply_form_data(None, payload)
        if message:
            flash(message, "error")
        else:
            flash("Puzzle created.", "success")
            return redirect(url_for("connections_admin.edit_puzzle", puzzle_id=puzzle.id))
    return render_template("connections_admin/editor.html", puzzle=None, form_data=form_data, csrf_token=_csrf_token())


@connections_admin_bp.route("/puzzles/<int:puzzle_id>")
@login_required
@admin_required
def puzzle_detail(puzzle_id):
    puzzle = db.session.get(ConnectionsPuzzle, puzzle_id)
    if puzzle is None:
        abort(404)
    return render_template("connections_admin/detail.html", puzzle=puzzle, csrf_token=_csrf_token())


@connections_admin_bp.route("/puzzles/<int:puzzle_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_puzzle(puzzle_id):
    puzzle = db.session.get(ConnectionsPuzzle, puzzle_id)
    if puzzle is None:
        abort(404)
    form_data = _puzzle_data(puzzle)
    if request.method == "POST":
        payload = _payload_from_form()
        _require_csrf(payload)
        form_data.update(payload)
        message, saved_puzzle = _apply_form_data(puzzle, payload)
        if message:
            flash(message, "error")
        else:
            puzzle = saved_puzzle
            flash("Puzzle updated.", "success")
            return redirect(url_for("connections_admin.edit_puzzle", puzzle_id=puzzle.id))
    return render_template("connections_admin/editor.html", puzzle=puzzle, form_data=form_data, csrf_token=_csrf_token())


@connections_admin_bp.route("/puzzles/<int:puzzle_id>/archive", methods=["POST"])
@login_required
@admin_required
def archive_puzzle(puzzle_id):
    _require_csrf()
    puzzle = db.session.get(ConnectionsPuzzle, puzzle_id)
    if puzzle is None:
        abort(404)
    puzzle.status = "archived"
    puzzle.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Connections puzzle archive failed")
        flash("The puzzle could not be archived. Please try again.", "error")
        return redirect(url_for("connections_admin.library"))
    flash("Puzzle archived.", "success")
    return redirect(url_for("connections_admin.library"))


@connections_admin_bp.route("/puzzles/<int:puzzle_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_puzzle(puzzle_id):
    _require_csrf()
    puzzle = db.session.get(ConnectionsPuzzle, puzzle_id)
    if puzzle is None:
        abort(404)
    if _puzzle_is_referenced(puzzle.id):
        flash("This puzzle is referenced by game records and cannot be deleted; archive it instead.", "error")
    else:
        try:
            db.session.delete(puzzle)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Connections puzzle deletion failed")
            flash("The puzzle could not be deleted. Please try again.", "error")
            return redirect(url_for("connections_admin.library"))
        flash("Puzzle deleted.", "success")
    return redirect(url_for("connections_admin.library"))


@connections_admin_bp.route("/api/puzzles", methods=["GET", "POST"])
@login_required
@admin_required
def puzzles_api():
    if request.method == "GET":
        puzzles = ConnectionsPuzzle.query.order_by(ConnectionsPuzzle.id.desc()).limit(100).all()
        return jsonify({"puzzles": [_serialize_puzzle(puzzle) for puzzle in puzzles]})
    payload = request.get_json(silent=True)
    _require_csrf(payload)
    try:
        puzzle = _save_puzzle(payload)
        db.session.commit()
        db.session.refresh(puzzle)
        return jsonify({"puzzle": _serialize_puzzle(puzzle)}), 201
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Connections API create failed")
        return jsonify({"error": "The puzzle could not be saved."}), 500


@connections_admin_bp.route("/api/puzzles/<int:puzzle_id>", methods=["GET", "PUT", "DELETE"])
@login_required
@admin_required
def puzzle_api(puzzle_id):
    puzzle = db.session.get(ConnectionsPuzzle, puzzle_id)
    if puzzle is None:
        abort(404)
    if request.method == "GET":
        return jsonify({"puzzle": _serialize_puzzle(puzzle)})
    payload = request.get_json(silent=True) if request.method == "PUT" else None
    _require_csrf(payload)
    if request.method == "PUT":
        try:
            _save_puzzle(payload, puzzle=puzzle)
            db.session.commit()
            db.session.refresh(puzzle)
            return jsonify({"puzzle": _serialize_puzzle(puzzle)})
        except ValueError as exc:
            db.session.rollback()
            return jsonify({"error": str(exc)}), 400
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Connections API update failed")
            return jsonify({"error": "The puzzle could not be saved."}), 500
    if _puzzle_is_referenced(puzzle.id):
        return jsonify({"error": "This puzzle is referenced and cannot be deleted; archive it instead."}), 409
    try:
        db.session.delete(puzzle)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Connections API delete failed")
        return jsonify({"error": "The puzzle could not be deleted."}), 500
    return jsonify({"deleted": True})


def register_connections_admin(app):
    app.register_blueprint(connections_admin_bp)

    @app.cli.command("grant-connections-admin")
    @click.argument("email")
    def grant_connections_admin(email):
        """Grant admin access after explicit operator confirmation."""
        from pvp.schema import upgrade_pvp_schema

        db.create_all()
        upgrade_pvp_schema()
        user = User.query.filter(func.lower(User.email) == email.strip().lower()).first()
        if user is None:
            raise click.ClickException("No existing user matches that email.")
        if user.is_admin:
            click.echo("This account already has Connections administrator access.")
            return
        click.echo(f"Account selected: {user.name} ({user.email})")
        if not click.confirm("Grant Connections administrator access to this account?"):
            click.echo("No changes made.")
            return
        user.is_admin = True
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            raise click.ClickException("Administrator access could not be saved.") from None
        click.echo("Connections administrator access granted.")
