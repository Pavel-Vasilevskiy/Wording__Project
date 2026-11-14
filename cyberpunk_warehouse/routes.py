from functools import wraps
import re
import secrets

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_file, session, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from cyberpunk_warehouse.models import (
    CyberpunkAsset,
    CyberpunkCategory,
    CyberpunkCustomer,
    CyberpunkCustomerArtwork,
    CyberpunkGameMode,
    CyberpunkLevel,
    CyberpunkOrder,
    CyberpunkProduct,
)
from cyberpunk_warehouse.services.assets import (
    AssetError,
    MAX_ASSET_BYTES,
    asset_storage_directory,
    process_uploaded_image,
    remove_asset_file,
    save_asset_bytes,
    validate_storage_reference,
)
from cyberpunk_warehouse.services.levels import (
    LAYOUT_VERSION, MAX_LAYOUT_CELLS, MAX_LAYOUT_DIMENSION,
    level_publish_errors, normalize_warehouse_layout,
)
from models import db


cyberpunk_warehouse_admin_bp = Blueprint(
    "cyberpunk_warehouse_admin",
    __name__,
    url_prefix="/admin/cyberpunk-warehouse",
)

PLANNED_SECTIONS = {
    "orders": ("Orders", "Order creation and product quantity editing are planned."),
    "settings": ("Game Settings", "Global gameplay tuning is planned after level configuration settles."),
    "analytics": ("Analytics", "Usage and performance reporting is planned after gameplay data exists."),
}
RARITIES = {"common", "uncommon", "rare", "epic", "legendary"}
CUSTOMER_TYPES = {"standard", "business", "courier", "vip", "robot"}
ASSET_TYPES = {
    "product": "Product artwork",
    "customer": "Customer default artwork",
    "customer_emotion": "Customer emotion artwork",
    "warehouse_background": "Warehouse background",
    "shelf": "Shelf artwork",
    "ui": "UI / category icon",
    "animation": "Animation frame",
}
KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PAGE_SIZE = 30


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _csrf_token():
    token = session.get("cyberpunk_admin_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["cyberpunk_admin_csrf"] = token
    return token


def _check_csrf():
    expected = session.get("cyberpunk_admin_csrf", "")
    supplied = request.form.get("csrf_token", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        abort(400, description="Invalid form security token.")


@cyberpunk_warehouse_admin_bp.context_processor
def inject_admin_context():
    return {"cw_csrf_token": _csrf_token, "cw_page_url": _page_url, "cw_asset_url": lambda asset: url_for(
        "cyberpunk_warehouse_admin.serve_asset", asset_id=asset.id, v=asset.storage_path
    ) if asset else None}


def _page_url(page):
    values = request.args.to_dict()
    values["page"] = page
    return url_for(request.endpoint, **(request.view_args or {}), **values)


def _text(name, label, maximum, required=False):
    value = request.form.get(name, "").strip()
    if required and not value:
        raise ValueError(f"{label} is required.")
    if len(value) > maximum:
        raise ValueError(f"{label} must be {maximum} characters or fewer.")
    return value


def _integer(name, label, minimum=None, default=None):
    raw = request.form.get(name, "").strip()
    if not raw and default is not None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    return value


def _optional_asset(asset_id, allowed_types):
    if not asset_id:
        return None
    try:
        asset_id = int(asset_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select a valid artwork asset.") from exc
    asset = db.session.get(CyberpunkAsset, asset_id)
    if asset is None or not asset.is_active or asset.asset_type not in allowed_types:
        raise ValueError("Select an active artwork asset of the required type.")
    return asset


@cyberpunk_warehouse_admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    totals = {
        "levels": CyberpunkLevel.query.count(),
        "products": CyberpunkProduct.query.count(),
        "categories": CyberpunkCategory.query.count(),
        "customers": CyberpunkCustomer.query.count(),
        "orders": CyberpunkOrder.query.count(),
        "assets": CyberpunkAsset.query.count(),
        "modes": CyberpunkGameMode.query.count(),
    }
    sections = [
        {"key": "products", "title": "Products", "state": "Available", "url": url_for("cyberpunk_warehouse_admin.products"), "available": True},
        {"key": "categories", "title": "Categories", "state": "Available", "url": url_for("cyberpunk_warehouse_admin.categories"), "available": True},
        {"key": "customers", "title": "Customers", "state": "Available", "url": url_for("cyberpunk_warehouse_admin.customers"), "available": True},
        {"key": "assets", "title": "Assets", "state": "Available", "url": url_for("cyberpunk_warehouse_admin.assets"), "available": True},
        {"key": "levels", "title": "Levels", "state": "Foundation", "url": url_for("cyberpunk_warehouse_admin.levels"), "available": True},
        {"key": "game-modes", "title": "Game Modes", "state": "Foundation", "url": url_for("cyberpunk_warehouse_admin.game_modes"), "available": True},
    ]
    sections.extend(
        {"key": key, "title": title, "state": "Planned", "url": url_for("cyberpunk_warehouse_admin.planned_section", section_key=key), "available": False}
        for key, (title, _description) in PLANNED_SECTIONS.items()
    )
    return render_template("cyberpunk_warehouse_admin/dashboard.html", totals=totals, sections=sections)


@cyberpunk_warehouse_admin_bp.route("/products")
@login_required
@admin_required
def products():
    query = CyberpunkProduct.query.outerjoin(CyberpunkCategory, CyberpunkProduct.category_id == CyberpunkCategory.id)
    search = request.args.get("q", "").strip()[:100]
    category_id = request.args.get("category", "")
    status = request.args.get("status", "active")
    if search:
        query = query.filter(or_(CyberpunkProduct.name.ilike(f"%{search}%"), CyberpunkProduct.internal_key.ilike(f"%{search}%")))
    if category_id.isdigit():
        query = query.filter(CyberpunkProduct.category_id == int(category_id))
    if status == "active":
        query = query.filter(CyberpunkProduct.is_active.is_(True))
    elif status == "archived":
        query = query.filter(CyberpunkProduct.is_active.is_(False))
    sort = request.args.get("sort", "sort_order")
    sort_column = {"name": CyberpunkProduct.name, "rarity": CyberpunkProduct.rarity, "weight": CyberpunkProduct.spawn_weight}.get(sort, CyberpunkProduct.sort_order)
    direction = request.args.get("direction", "asc")
    sort_column = sort_column.desc() if direction == "desc" else sort_column.asc()
    page = _page_number()
    pagination = query.order_by(sort_column, CyberpunkProduct.id).paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    return render_template(
        "cyberpunk_warehouse_admin/products.html", pagination=pagination, categories=CyberpunkCategory.query.order_by(CyberpunkCategory.sort_order, CyberpunkCategory.name).all(),
        search=search, selected_category=category_id, selected_status=status, sort=sort, direction=direction,
    )


def _page_number():
    try:
        return max(1, int(request.args.get("page", "1")))
    except (TypeError, ValueError):
        return 1


def _product_values(product):
    product.name = _text("name", "Name", 120, True)
    product.internal_key = _text("internal_key", "Internal key", 80, True).lower()
    if not KEY_RE.fullmatch(product.internal_key):
        raise ValueError("Internal key may contain lowercase letters, numbers, and single hyphens.")
    product.description = _text("description", "Description", 600)
    product.rarity = request.form.get("rarity", "common")
    if product.rarity not in RARITIES:
        raise ValueError("Select a valid rarity.")
    product.spawn_weight = _integer("spawn_weight", "Spawn weight", 1)
    product.sort_order = _integer("sort_order", "Sort order", 0, 0)
    product.is_active = request.form.get("is_active") == "1"
    category_id = request.form.get("category_id", "").strip()
    category = None
    if category_id:
        try:
            category = db.session.get(CyberpunkCategory, int(category_id))
        except ValueError as exc:
            raise ValueError("Select a valid category.") from exc
        if category is None:
            raise ValueError("Select a valid category.")
    product.product_category = category
    product.category = category.name if category else ""
    product.image_asset = _optional_asset(request.form.get("image_asset_id"), {"product"})


@cyberpunk_warehouse_admin_bp.route("/products/new", methods=["GET", "POST"])
@login_required
@admin_required
def product_new():
    product = CyberpunkProduct(name="", internal_key="", rarity="common", spawn_weight=1, is_active=True)
    if request.method == "POST":
        _check_csrf()
        try:
            _product_values(product)
            db.session.add(product)
            db.session.commit()
            flash("Product created.", "success")
            return redirect(url_for("cyberpunk_warehouse_admin.products"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            flash("That internal key is already in use.", "error")
    return render_template("cyberpunk_warehouse_admin/product_form.html", product=product, categories=_categories(), assets=_assets("product"), form_title="New product")


@cyberpunk_warehouse_admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def product_edit(product_id):
    product = db.session.get(CyberpunkProduct, product_id)
    if product is None:
        abort(404)
    if request.method == "POST":
        _check_csrf()
        try:
            _product_values(product)
            db.session.commit()
            flash("Product updated.", "success")
            return redirect(url_for("cyberpunk_warehouse_admin.products"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            product = db.session.get(CyberpunkProduct, product_id)
        except IntegrityError:
            db.session.rollback()
            flash("That internal key is already in use.", "error")
            product = db.session.get(CyberpunkProduct, product_id)
    return render_template("cyberpunk_warehouse_admin/product_form.html", product=product, categories=_categories(), assets=_assets("product"), form_title="Edit product")


@cyberpunk_warehouse_admin_bp.post("/products/<int:product_id>/status")
@login_required
@admin_required
def product_status(product_id):
    _check_csrf()
    product = db.session.get(CyberpunkProduct, product_id)
    if product is None:
        abort(404)
    product.is_active = request.form.get("is_active") == "1"
    db.session.commit()
    flash("Product status updated.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.products"))


@cyberpunk_warehouse_admin_bp.post("/products/<int:product_id>/delete")
@login_required
@admin_required
def product_delete(product_id):
    _check_csrf()
    product = db.session.get(CyberpunkProduct, product_id)
    if product is None:
        abort(404)
    if product.levels or product.order_items:
        flash("This product is used by a level or order. Archive it instead of deleting it.", "error")
    else:
        db.session.delete(product)
        db.session.commit()
        flash("Product deleted.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.products"))


def _categories():
    return CyberpunkCategory.query.order_by(CyberpunkCategory.sort_order, CyberpunkCategory.name).all()


def _assets(asset_type=None):
    query = CyberpunkAsset.query.filter_by(is_active=True)
    if asset_type:
        query = query.filter_by(asset_type=asset_type)
    return query.order_by(CyberpunkAsset.created_at.desc(), CyberpunkAsset.id.desc()).limit(200).all()


@cyberpunk_warehouse_admin_bp.route("/categories")
@login_required
@admin_required
def categories():
    query = CyberpunkCategory.query.outerjoin(CyberpunkProduct).group_by(CyberpunkCategory.id)
    search = request.args.get("q", "").strip()[:100]
    if search:
        query = query.filter(or_(CyberpunkCategory.name.ilike(f"%{search}%"), CyberpunkCategory.internal_key.ilike(f"%{search}%")))
    records = query.order_by(CyberpunkCategory.sort_order, CyberpunkCategory.name).all()
    counts = dict(db.session.query(CyberpunkProduct.category_id, func.count(CyberpunkProduct.id)).filter(CyberpunkProduct.category_id.isnot(None)).group_by(CyberpunkProduct.category_id).all())
    return render_template("cyberpunk_warehouse_admin/categories.html", categories=records, product_counts=counts, search=search)


def _category_values(category):
    category.name = _text("name", "Name", 100, True)
    category.internal_key = _text("internal_key", "Internal key", 80, True).lower()
    if not KEY_RE.fullmatch(category.internal_key):
        raise ValueError("Internal key may contain lowercase letters, numbers, and single hyphens.")
    category.description = _text("description", "Description", 600)
    category.sort_order = _integer("sort_order", "Sort order", 0, 0)
    category.is_active = request.form.get("is_active") == "1"
    category.icon_asset = _optional_asset(request.form.get("icon_asset_id"), {"ui"})


@cyberpunk_warehouse_admin_bp.route("/categories/new", methods=["GET", "POST"])
@login_required
@admin_required
def category_new():
    category = CyberpunkCategory(name="", internal_key="")
    if request.method == "POST":
        _check_csrf()
        try:
            _category_values(category)
            db.session.add(category)
            db.session.commit()
            flash("Category created.", "success")
            return redirect(url_for("cyberpunk_warehouse_admin.categories"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            flash("That internal key is already in use.", "error")
    return render_template("cyberpunk_warehouse_admin/category_form.html", category=category, assets=_assets("ui"), form_title="New category")


@cyberpunk_warehouse_admin_bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def category_edit(category_id):
    category = db.session.get(CyberpunkCategory, category_id)
    if category is None:
        abort(404)
    if request.method == "POST":
        _check_csrf()
        try:
            _category_values(category)
            db.session.commit()
            flash("Category updated.", "success")
            return redirect(url_for("cyberpunk_warehouse_admin.categories"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            category = db.session.get(CyberpunkCategory, category_id)
        except IntegrityError:
            db.session.rollback()
            flash("That internal key is already in use.", "error")
            category = db.session.get(CyberpunkCategory, category_id)
    return render_template("cyberpunk_warehouse_admin/category_form.html", category=category, assets=_assets("ui"), form_title="Edit category")


@cyberpunk_warehouse_admin_bp.post("/categories/<int:category_id>/delete")
@login_required
@admin_required
def category_delete(category_id):
    _check_csrf()
    category = db.session.get(CyberpunkCategory, category_id)
    if category is None:
        abort(404)
    if category.products:
        flash("Reassign or remove this category's products before deleting it.", "error")
    else:
        db.session.delete(category)
        db.session.commit()
        flash("Category deleted.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.categories"))


@cyberpunk_warehouse_admin_bp.route("/customers")
@login_required
@admin_required
def customers():
    query = CyberpunkCustomer.query
    search = request.args.get("q", "").strip()[:100]
    status = request.args.get("status", "active")
    customer_type = request.args.get("type", "")
    if search:
        query = query.filter(or_(CyberpunkCustomer.name.ilike(f"%{search}%"), CyberpunkCustomer.internal_key.ilike(f"%{search}%")))
    if status == "active":
        query = query.filter(CyberpunkCustomer.is_active.is_(True))
    elif status == "archived":
        query = query.filter(CyberpunkCustomer.is_active.is_(False))
    if customer_type:
        query = query.filter(CyberpunkCustomer.customer_type == customer_type)
    page = _page_number()
    pagination = query.order_by(CyberpunkCustomer.sort_order, CyberpunkCustomer.name).paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    return render_template("cyberpunk_warehouse_admin/customers.html", pagination=pagination, search=search, selected_status=status, selected_type=customer_type, customer_types=sorted(CUSTOMER_TYPES))


def _customer_values(customer):
    was_active = bool(customer.is_active)
    customer.name = _text("name", "Name", 120, True)
    customer.internal_key = _text("internal_key", "Internal key", 80, True).lower()
    if not KEY_RE.fullmatch(customer.internal_key):
        raise ValueError("Internal key may contain lowercase letters, numbers, and single hyphens.")
    customer.description = _text("description", "Description", 600)
    customer.customer_type = _text("customer_type", "Customer type", 40, True).lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", customer.customer_type):
        raise ValueError("Customer type must use letters, numbers, underscores, or hyphens.")
    customer.default_waiting_seconds = _integer("default_waiting_seconds", "Default waiting time", 0, 60)
    customer.sort_order = _integer("sort_order", "Sort order", 0, 0)
    customer.is_active = request.form.get("is_active") == "1"
    customer.character_artwork = _optional_asset(request.form.get("character_artwork_id"), {"customer"})
    customer.happy_artwork = _optional_asset(request.form.get("happy_artwork_id"), {"customer_emotion"})
    customer.impatient_artwork = _optional_asset(request.form.get("impatient_artwork_id"), {"customer_emotion"})
    customer.disappointed_artwork = _optional_asset(request.form.get("disappointed_artwork_id"), {"customer_emotion"})
    if customer.is_active and not was_active and customer.character_artwork is None:
        raise ValueError("Default customer artwork is required before activating a customer.")

    desired_states = {
        "default": customer.character_artwork,
        "happy": customer.happy_artwork,
        "impatient": customer.impatient_artwork,
        "disappointed": customer.disappointed_artwork,
    }
    extra_keys = set()
    for index in range(3):
        state_key = request.form.get(f"extra_state_key_{index}", "").strip().lower()
        asset_id = request.form.get(f"extra_state_asset_{index}", "").strip()
        if not state_key and not asset_id:
            continue
        if not state_key or not KEY_RE.fullmatch(state_key) or len(state_key) > 60:
            raise ValueError("Additional state keys must use lowercase letters, numbers, and hyphens.")
        if state_key in desired_states or state_key in extra_keys:
            raise ValueError("Customer artwork state keys must be unique.")
        extra_asset = _optional_asset(asset_id, {"customer_emotion", "animation"})
        if extra_asset is None:
            raise ValueError(f"Choose artwork for the additional '{state_key}' state.")
        desired_states[state_key] = extra_asset
        extra_keys.add(state_key)

    existing_states = {state.state_key: state for state in customer.artwork_states}
    for state_key, asset in desired_states.items():
        existing = existing_states.pop(state_key, None)
        if asset is None:
            if existing is not None:
                db.session.delete(existing)
        elif existing is None:
            customer.artwork_states.append(CyberpunkCustomerArtwork(state_key=state_key, asset=asset))
        else:
            existing.asset = asset
    for stale_state in existing_states.values():
        db.session.delete(stale_state)


def _additional_artwork_states(customer):
    standard = {"default", "happy", "impatient", "disappointed"}
    return [
        {"state_key": state.state_key, "asset_id": state.asset_id}
        for state in customer.artwork_states
        if state.state_key not in standard
    ][:3]


@cyberpunk_warehouse_admin_bp.route("/customers/new", methods=["GET", "POST"])
@login_required
@admin_required
def customer_new():
    customer = CyberpunkCustomer(name="", internal_key="", is_active=False)
    if request.method == "POST":
        _check_csrf()
        try:
            _customer_values(customer)
            db.session.add(customer)
            db.session.commit()
            flash("Customer created.", "success")
            return redirect(url_for("cyberpunk_warehouse_admin.customers"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            flash("That internal key is already in use.", "error")
    return render_template("cyberpunk_warehouse_admin/customer_form.html", customer=customer, assets=_assets(), additional_states=_additional_artwork_states(customer), form_title="New customer", customer_types=sorted(CUSTOMER_TYPES))


@cyberpunk_warehouse_admin_bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def customer_edit(customer_id):
    customer = db.session.get(CyberpunkCustomer, customer_id)
    if customer is None:
        abort(404)
    if request.method == "POST":
        _check_csrf()
        try:
            _customer_values(customer)
            db.session.commit()
            flash("Customer updated.", "success")
            return redirect(url_for("cyberpunk_warehouse_admin.customers"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            customer = db.session.get(CyberpunkCustomer, customer_id)
        except IntegrityError:
            db.session.rollback()
            flash("That internal key is already in use.", "error")
            customer = db.session.get(CyberpunkCustomer, customer_id)
    return render_template("cyberpunk_warehouse_admin/customer_form.html", customer=customer, assets=_assets(), additional_states=_additional_artwork_states(customer), form_title="Edit customer", customer_types=sorted(CUSTOMER_TYPES))


@cyberpunk_warehouse_admin_bp.post("/customers/<int:customer_id>/status")
@login_required
@admin_required
def customer_status(customer_id):
    _check_csrf()
    customer = db.session.get(CyberpunkCustomer, customer_id)
    if customer is None:
        abort(404)
    activate = request.form.get("is_active") == "1"
    if activate and customer.character_artwork is None:
        flash("Add default artwork before activating this customer.", "error")
    else:
        customer.is_active = activate
        db.session.commit()
        flash("Customer status updated.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.customers"))


@cyberpunk_warehouse_admin_bp.post("/customers/<int:customer_id>/delete")
@login_required
@admin_required
def customer_delete(customer_id):
    _check_csrf()
    customer = db.session.get(CyberpunkCustomer, customer_id)
    if customer is None:
        abort(404)
    used_by_level = CyberpunkLevel.query.filter_by(customer_id=customer.id).first()
    used_by_order = CyberpunkOrder.query.filter_by(customer_id=customer.id).first()
    if used_by_level or used_by_order:
        flash("This customer is assigned to a level or order. Archive it instead of deleting it.", "error")
    else:
        db.session.delete(customer)
        db.session.commit()
        flash("Customer deleted.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.customers"))


@cyberpunk_warehouse_admin_bp.route("/assets", methods=["GET", "POST"])
@login_required
@admin_required
def assets():
    if request.method == "POST":
        _check_csrf()
        upload = request.files.get("image")
        asset_type = request.form.get("asset_type", "")
        alt_text = request.form.get("alt_text", "").strip()
        if asset_type not in ASSET_TYPES:
            flash("Select a valid asset type.", "error")
        elif len(alt_text) > 180:
            flash("Alt text must be 180 characters or fewer.", "error")
        elif upload is None or not upload.filename:
            flash("Choose an image to upload.", "error")
        else:
            saved_name = None
            try:
                max_bytes = int(current_app.config.get("CYBERPUNK_ASSET_MAX_BYTES", MAX_ASSET_BYTES))
                content, (width, height) = process_uploaded_image(upload, max_bytes=max_bytes)
                storage_dir = asset_storage_directory(current_app.config)
                saved_name = save_asset_bytes(storage_dir, content)
                asset = CyberpunkAsset(
                    asset_key=f"{asset_type}:{saved_name[:-5]}", asset_type=asset_type,
                    storage_path=saved_name, media_type="image/webp", alt_text=alt_text,
                    width=width, height=height, file_size=len(content), created_by=current_user.id,
                )
                db.session.add(asset)
                db.session.commit()
                flash("Artwork uploaded and ready to use.", "success")
                return redirect(url_for("cyberpunk_warehouse_admin.assets"))
            except (AssetError, RuntimeError) as exc:
                db.session.rollback()
                if saved_name:
                    try:
                        remove_asset_file(asset_storage_directory(current_app.config), saved_name)
                    except (AssetError, RuntimeError, OSError):
                        pass
                flash(str(exc), "error")
            except Exception:
                db.session.rollback()
                if saved_name:
                    try:
                        remove_asset_file(asset_storage_directory(current_app.config), saved_name)
                    except Exception:
                        pass
                current_app.logger.exception("Could not save Cyberpunk Warehouse asset")
                flash("Upload failed. Please try again.", "error")
    asset_type = request.args.get("type", "")
    query = CyberpunkAsset.query
    if asset_type in ASSET_TYPES:
        query = query.filter_by(asset_type=asset_type)
    page = _page_number()
    pagination = query.order_by(CyberpunkAsset.created_at.desc(), CyberpunkAsset.id.desc()).paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    return render_template("cyberpunk_warehouse_admin/assets.html", pagination=pagination, asset_types=ASSET_TYPES, selected_type=asset_type, max_asset_mb=MAX_ASSET_BYTES // (1024 * 1024))


def _asset_reference_count(asset):
    return sum((
        CyberpunkProduct.query.filter_by(image_asset_id=asset.id).count(),
        CyberpunkCategory.query.filter_by(icon_asset_id=asset.id).count(),
        CyberpunkCustomer.query.filter(or_(
            CyberpunkCustomer.character_artwork_id == asset.id,
            CyberpunkCustomer.happy_artwork_id == asset.id,
            CyberpunkCustomer.impatient_artwork_id == asset.id,
            CyberpunkCustomer.disappointed_artwork_id == asset.id,
        )).count(),
        CyberpunkCustomerArtwork.query.filter_by(asset_id=asset.id).count(),
        CyberpunkLevel.query.filter(or_(CyberpunkLevel.background_asset_id == asset.id, CyberpunkLevel.shelf_asset_id == asset.id)).count(),
    ))


@cyberpunk_warehouse_admin_bp.post("/assets/<int:asset_id>/replace")
@login_required
@admin_required
def asset_replace(asset_id):
    _check_csrf()
    asset = db.session.get(CyberpunkAsset, asset_id)
    if asset is None:
        abort(404)
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        flash("Choose a replacement image.", "error")
        return redirect(url_for("cyberpunk_warehouse_admin.assets"))
    saved_name = None
    try:
        max_bytes = int(current_app.config.get("CYBERPUNK_ASSET_MAX_BYTES", MAX_ASSET_BYTES))
        content, (width, height) = process_uploaded_image(upload, max_bytes=max_bytes)
        storage_dir = asset_storage_directory(current_app.config)
        saved_name = save_asset_bytes(storage_dir, content)
        old_storage_path = asset.storage_path
        asset.storage_path = saved_name
        asset.media_type = "image/webp"
        asset.width = width
        asset.height = height
        asset.file_size = len(content)
        db.session.commit()
    except AssetError as exc:
        db.session.rollback()
        if saved_name:
            try:
                remove_asset_file(storage_dir, saved_name)
            except Exception:
                current_app.logger.exception("Could not clean up failed replacement asset")
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        if saved_name:
            try:
                remove_asset_file(storage_dir, saved_name)
            except Exception:
                current_app.logger.exception("Could not clean up failed replacement asset")
        current_app.logger.exception("Could not replace Cyberpunk Warehouse asset")
        flash("Replacement failed. Please try again.", "error")
    else:
        try:
            remove_asset_file(storage_dir, old_storage_path)
        except (AssetError, OSError):
            current_app.logger.exception("Replacement succeeded but previous file cleanup failed")
        flash("Artwork replaced.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.assets"))


@cyberpunk_warehouse_admin_bp.post("/assets/<int:asset_id>/details")
@login_required
@admin_required
def asset_update_details(asset_id):
    _check_csrf()
    asset = db.session.get(CyberpunkAsset, asset_id)
    if asset is None:
        abort(404)
    alt_text = request.form.get("alt_text", "").strip()
    if len(alt_text) > 180:
        flash("Alt text must be 180 characters or fewer.", "error")
    elif request.form.get("is_active") != "1" and _asset_reference_count(asset):
        flash("Artwork assigned to catalog records cannot be deactivated. Replace its assignments first.", "error")
    else:
        asset.alt_text = alt_text
        asset.is_active = request.form.get("is_active") == "1"
        db.session.commit()
        flash("Asset details updated.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.assets"))


@cyberpunk_warehouse_admin_bp.post("/assets/<int:asset_id>/delete")
@login_required
@admin_required
def asset_delete(asset_id):
    _check_csrf()
    asset = db.session.get(CyberpunkAsset, asset_id)
    if asset is None:
        abort(404)
    if _asset_reference_count(asset):
        flash("This artwork is still referenced. Replace or remove its assignments before deleting it.", "error")
    else:
        try:
            storage_dir = asset_storage_directory(current_app.config)
            storage_path = asset.storage_path
            db.session.delete(asset)
            db.session.commit()
        except (AssetError, RuntimeError, OSError):
            db.session.rollback()
            current_app.logger.exception("Could not delete Cyberpunk Warehouse asset")
            flash("Could not delete this artwork safely.", "error")
        else:
            try:
                remove_asset_file(storage_dir, storage_path)
                flash("Artwork deleted.", "success")
            except (AssetError, OSError):
                current_app.logger.exception("Asset row was deleted but file cleanup failed")
                flash("Artwork record deleted. The stored file could not be cleaned up automatically.", "error")
    return redirect(url_for("cyberpunk_warehouse_admin.assets"))


@cyberpunk_warehouse_admin_bp.get("/assets/<int:asset_id>/image")
@login_required
@admin_required
def serve_asset(asset_id):
    asset = db.session.get(CyberpunkAsset, asset_id)
    if asset is None:
        abort(404)
    try:
        relative = validate_storage_reference(asset.storage_path)
        root = asset_storage_directory(current_app.config)
        target = (root / "/".join(relative.parts)).resolve()
        if target.parent != root or not target.is_file():
            abort(404)
        return send_file(target, mimetype="image/webp", conditional=True, max_age=3600, download_name=f"asset-{asset.id}.webp")
    except (AssetError, RuntimeError, OSError):
        abort(404)


@cyberpunk_warehouse_admin_bp.route("/levels")
@login_required
@admin_required
def levels():
    levels = CyberpunkLevel.query.order_by(CyberpunkLevel.sort_order, CyberpunkLevel.level_number, CyberpunkLevel.id).limit(100).all()
    return render_template("cyberpunk_warehouse_admin/levels.html", levels=levels)


LEVEL_DIFFICULTIES = {"easy", "normal", "hard", "expert"}


def _level_editor_context(level=None, errors=None, form_values=None):
    products = CyberpunkProduct.query.filter_by(is_active=True).order_by(CyberpunkProduct.sort_order, CyberpunkProduct.name).all()
    product_data = [{
        "id": product.id, "name": product.name,
        "image_url": url_for("cyberpunk_warehouse_admin.serve_asset", asset_id=product.image_asset.id,
                              v=product.image_asset.storage_path) if product.image_asset else "",
        "category_id": product.category_id, "rarity": product.rarity,
        "spawn_weight": product.spawn_weight,
    } for product in products]
    categories = CyberpunkCategory.query.filter_by(is_active=True).order_by(CyberpunkCategory.sort_order, CyberpunkCategory.name).all()
    customers = CyberpunkCustomer.query.filter_by(is_active=True).order_by(CyberpunkCustomer.sort_order, CyberpunkCustomer.name).all()
    modes = CyberpunkGameMode.query.filter_by(is_active=True).order_by(CyberpunkGameMode.id).all()
    assets = CyberpunkAsset.query.filter_by(is_active=True).order_by(CyberpunkAsset.id.desc()).limit(200).all()
    errors = list(errors or [])
    layout_locked = False
    layout = {"version": LAYOUT_VERSION, "rows": 10, "columns": 10, "shelves": []}
    try:
        if request.method == "POST" and request.form.get("layout_rows", "").isdigit() and request.form.get("layout_columns", "").isdigit():
            rows = min(MAX_LAYOUT_DIMENSION, int(request.form["layout_rows"]))
            columns = min(MAX_LAYOUT_DIMENSION, int(request.form["layout_columns"]))
            shelves = []
            for row in range(rows):
                cells = []
                for column in range(columns):
                    slots = [request.form.get(f"cell_{row}_{column}_slot_{slot}", "").strip() or None for slot in range(3)]
                    cells.append({"row": row, "column": column, "slots": [int(value) if value and value.isdigit() else None for value in slots]})
                shelves.append({"id": f"shelf-{row + 1}", "name": f"Shelf {row + 1}", "cells": cells})
            layout = {"version": LAYOUT_VERSION, "rows": rows, "columns": columns, "shelves": shelves}
        elif level and level.warehouse_layout:
            layout = normalize_warehouse_layout(level.warehouse_layout)
    except ValueError as exc:
        errors.append(str(exc))
        layout_locked = True
    return render_template("cyberpunk_warehouse_admin/level_editor.html", level=level, errors=errors,
                           form_values=form_values or {}, products=products, categories=categories, customers=customers,
                           modes=modes, assets=assets, layout_data=layout, product_data=product_data,
                           layout_locked=layout_locked)


def _level_from_form(existing=None, *, allow_key_owner_id=None):
    from cyberpunk_warehouse.models import CyberpunkOrderItem

    level = existing or CyberpunkLevel()
    if request.form.get("layout_conversion_locked") == "1":
        raise ValueError("This legacy layout cannot be represented without losing stock. The saved level was left unchanged.")
    previous_mode_key = existing.game_mode.key if existing is not None and existing.game_mode else None
    previous_mode_configuration = dict(existing.mode_configuration or {}) if existing is not None else {}
    level.name = _text("name", "Level name", 120, required=True)
    key = _text("internal_key", "Internal key", 100, required=True).lower()
    if not KEY_RE.fullmatch(key):
        raise ValueError("Internal key may contain lowercase letters, numbers and single hyphens.")
    other = CyberpunkLevel.query.filter(CyberpunkLevel.internal_key == key)
    excluded_id = existing.id if existing is not None else allow_key_owner_id
    if excluded_id:
        other = other.filter(CyberpunkLevel.id != excluded_id)
    if other.first():
        raise ValueError("That internal key is already in use.")
    level.internal_key = key
    level.description = _text("description", "Description", 600)
    level.level_number = _integer("level_number", "Level number", minimum=1)
    level.sort_order = _integer("sort_order", "Sort order", minimum=0, default=0)
    level.difficulty = request.form.get("difficulty", "normal").strip().lower()
    if level.difficulty not in LEVEL_DIFFICULTIES:
        raise ValueError("Select a valid difficulty.")
    mode = db.session.get(CyberpunkGameMode, request.form.get("game_mode_id", type=int))
    if mode is None or not mode.is_active:
        raise ValueError("Select an active game mode.")
    level.game_mode = mode
    timer_field = "timed_time_limit_seconds" if mode.key == "timed" else "classic_time_limit_seconds"
    level.time_limit_seconds = _integer(timer_field, "Time limit", minimum=1) if request.form.get(timer_field, "").strip() else None
    level.customer_id = request.form.get("customer_id", type=int) or None
    if level.customer_id and db.session.get(CyberpunkCustomer, level.customer_id) is None:
        raise ValueError("Select a valid customer.")
    level.background_asset = _optional_asset(request.form.get("background_asset_id"), {"warehouse_background"})
    level.shelf_asset = _optional_asset(request.form.get("shelf_asset_id"), {"shelf"})
    level.preview_asset = _optional_asset(request.form.get("preview_asset_id"), {"warehouse_background", "ui"})

    if mode.key == "classic":
        config = {"objective": _text("classic_objective", "Objective", 300),
                  "lives": _integer("classic_lives", "Lives", minimum=1, default=3),
                  "scoring_rules": {"base_points": _integer("base_points", "Base points", minimum=0, default=0)}}
    elif mode.key == "timed":
        config = {"lives": _integer("timed_lives", "Lives", minimum=1),
                  "failure_conditions": [v.strip() for v in request.form.getlist("failure_conditions") if v.strip()],
                  "scoring_rules": {"base_points": _integer("base_points", "Base points", minimum=0, default=0),
                                    "time_bonus": _integer("time_bonus", "Time bonus", minimum=0, default=0)}}
    elif mode.key == "unlimited":
        raw = request.form.get("move_limit", "").strip()
        config = {"move_limit": _integer("move_limit", "Move limit", minimum=1) if raw else None,
                  "scoring_rules": {"base_points": _integer("base_points", "Base points", minimum=0, default=0)}}
    elif mode.key == "pvp":
        config = {"match_duration_seconds": _integer("match_duration_seconds", "Match duration", minimum=1),
                  "synchronization_settings": {"turn_based": request.form.get("turn_based") == "on"},
                  "competitive_scoring": {"points_per_order": _integer("points_per_order", "Points per order", minimum=0, default=0)},
                  "completion_conditions": [v.strip() for v in request.form.getlist("completion_conditions") if v.strip()]}
    else:
        raise ValueError("The selected game mode has no editor configuration.")
    all_mode_configs = dict(level.mode_configurations or {})
    if existing is not None and previous_mode_key and previous_mode_key not in all_mode_configs:
        # Preserve the pre-v1 editor's active mode settings on first edit.
        all_mode_configs[previous_mode_key] = previous_mode_configuration
    all_mode_configs[mode.key] = config
    level.mode_configurations = all_mode_configs
    level.mode_configuration = config
    level.gameplay_configuration = {
        "sorting_tray_capacity": _integer("sorting_tray_capacity", "Sorting tray capacity", minimum=3, default=7),
        "initial_lives": _integer("initial_lives", "Initial lives", minimum=1, default=3),
        "penalty_per_mistake": _integer("penalty_per_mistake", "Penalty", minimum=0, default=0),
        "customer_waiting_time_seconds": _integer("customer_waiting_time_seconds", "Customer waiting time", minimum=0, default=0),
        "delay_between_customers_seconds": _integer("delay_between_customers_seconds", "Customer delay", minimum=0, default=0),
    }

    rows = _integer("layout_rows", "Shelf count", minimum=1)
    columns = _integer("layout_columns", "Cells per shelf", minimum=1)
    if rows > MAX_LAYOUT_DIMENSION or columns > MAX_LAYOUT_DIMENSION or rows * columns > MAX_LAYOUT_CELLS:
        raise ValueError("Layout supports dimensions up to 20 × 20 (400 cells).")
    shelves, selected_products, stock = [], {}, {}
    for row in range(rows):
        cells = []
        for column in range(columns):
            slots = []
            for slot in range(3):
                value = request.form.get(f"cell_{row}_{column}_slot_{slot}", "").strip()
                if not value:
                    slots.append(None)
                    continue
                product_id = int(value) if value.isdigit() else 0
                product = db.session.get(CyberpunkProduct, product_id)
                if product is None or not product.is_active:
                    raise ValueError("Warehouse slots must reference active products.")
                slots.append(product.id)
                selected_products[product.id] = product
                stock[product.id] = stock.get(product.id, 0) + 1
            cells.append({"row": row, "column": column, "slots": slots})
        shelves.append({"id": f"shelf-{row + 1}", "name": f"Shelf {row + 1}", "cells": cells})
    level.warehouse_layout = {"version": LAYOUT_VERSION, "rows": rows, "columns": columns, "shelves": shelves}
    level.configuration_version = 2
    level.products = list(selected_products.values())

    item_count = _integer("order_items_count", "Order item count", minimum=0, default=0)
    order_lines = []
    seen = set()
    for index in range(min(item_count, 60)):
        product_value = request.form.get(f"order_product_{index}", "").strip()
        quantity_value = request.form.get(f"order_quantity_{index}", "").strip()
        if not product_value and not quantity_value:
            continue
        if not product_value or not quantity_value or not product_value.isdigit() or not quantity_value.isdigit():
            raise ValueError("Complete both product and quantity for each order row.")
        product_id, quantity = int(product_value), int(quantity_value)
        if quantity <= 0:
            raise ValueError("Order quantities must be positive whole numbers.")
        if product_id in seen:
            raise ValueError("Each order can list a product only once.")
        product = db.session.get(CyberpunkProduct, product_id)
        if product is None or not product.is_active:
            raise ValueError("Orders must reference active products.")
        seen.add(product_id)
        if quantity > stock.get(product_id, 0):
            raise ValueError(f"Requested quantity exceeds warehouse stock for {product.name}.")
        order_lines.append((product, quantity))

    # Current editor manages the first order; any future multi-customer model can build on this relation.
    for prior in list(level.orders):
        db.session.delete(prior)
    if existing is not None:
        db.session.flush()
    if order_lines:
        order = CyberpunkOrder(name=_text("order_name", "Order name", 120), display_order=1,
                               customer_id=level.customer_id, order_configuration={"version": 1})
        for product, quantity in order_lines:
            order.items.append(CyberpunkOrderItem(product=product, quantity=quantity))
        level.orders.append(order)
    return level


@cyberpunk_warehouse_admin_bp.route("/levels/new", methods=["GET", "POST"])
@login_required
@admin_required
def level_new():
    if request.method == "POST":
        _check_csrf()
        try:
            with db.session.no_autoflush:
                level = _level_from_form()
            requested_status = request.form.get("status", "draft")
            if requested_status not in {"draft", "published"}:
                raise ValueError("New levels can be saved as Draft or Published.")
            level.status = "draft"
            db.session.add(level)
            db.session.flush()
            if requested_status == "published":
                errors = level_publish_errors(level)
                if errors:
                    raise ValueError("Cannot publish: " + " ".join(errors))
                level.status = "published"
            db.session.commit()
            flash("Level created." if level.status == "draft" else "Level published.", "success")
            return redirect(url_for("cyberpunk_warehouse_admin.level_edit", level_id=level.id))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else "Level could not be saved. Check its key and level number.", "error")
            return _level_editor_context(errors=[str(exc)])
    return _level_editor_context()


@cyberpunk_warehouse_admin_bp.route("/levels/preview", methods=["POST"])
@login_required
@admin_required
def level_preview_unsaved():
    _check_csrf()
    try:
        with db.session.no_autoflush:
            owner_id = request.form.get("source_level_id", type=int)
            level = _level_from_form(allow_key_owner_id=owner_id)
            products_by_id = {product.id: product for product in CyberpunkProduct.query.all()}
        return render_template("cyberpunk_warehouse_admin/level_preview.html", level=level,
                               products_by_id=products_by_id, unsaved=True)
    except (ValueError, IntegrityError) as exc:
        flash(str(exc) if isinstance(exc, ValueError) else "Could not preview this configuration.", "error")
        return _level_editor_context(errors=[str(exc)])


@cyberpunk_warehouse_admin_bp.route("/levels/<int:level_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def level_edit(level_id):
    level = db.session.get(CyberpunkLevel, level_id)
    if level is None:
        abort(404)
    if request.method == "POST":
        _check_csrf()
        try:
            prior_status = level.status
            _level_from_form(level)
            action = request.form.get("action", "save")
            if action == "publish":
                if level.status == "archived":
                    raise ValueError("Restore an archived level as Draft before publishing it.")
                errors = level_publish_errors(level)
                if errors:
                    raise ValueError("Cannot publish: " + " ".join(errors))
                level.status = "published"
            elif action == "archive":
                level.status = "archived"
            elif action == "restore":
                level.status = "draft"
            elif action == "unpublish":
                level.status = "draft"
            else:
                if prior_status == "published":
                    errors = level_publish_errors(level)
                    if errors:
                        raise ValueError("Published level still has validation issues; unpublish before saving: " + " ".join(errors))
                level.status = prior_status
            db.session.commit()
            flash("Level updated.", "success")
            if action == "preview":
                return redirect(url_for("cyberpunk_warehouse_admin.level_preview", level_id=level.id))
            return redirect(url_for("cyberpunk_warehouse_admin.level_edit", level_id=level.id))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            level = db.session.get(CyberpunkLevel, level_id)
            flash(str(exc) if isinstance(exc, ValueError) else "Level could not be saved. Check its key and level number.", "error")
    return _level_editor_context(level=level)


@cyberpunk_warehouse_admin_bp.route("/levels/<int:level_id>/duplicate", methods=["POST"])
@login_required
@admin_required
def level_duplicate(level_id):
    from cyberpunk_warehouse.models import CyberpunkOrderItem

    _check_csrf()
    source = db.session.get(CyberpunkLevel, level_id)
    if source is None:
        abort(404)
    new_number = (db.session.query(func.max(CyberpunkLevel.level_number)).scalar() or 0) + 1
    copy = CyberpunkLevel(name=f"{source.name} Copy", internal_key=f"{source.internal_key}-copy-{secrets.token_hex(3)}",
        description=source.description, level_number=new_number, sort_order=source.sort_order,
        game_mode=source.game_mode, difficulty=source.difficulty, status="draft",
        mode_configuration=dict(source.mode_configuration or {}), mode_configurations=dict(source.mode_configurations or {}), order_configuration=dict(source.order_configuration or {}),
        warehouse_layout=dict(source.warehouse_layout or {}), gameplay_configuration=dict(source.gameplay_configuration or {}),
        configuration_version=source.configuration_version, time_limit_seconds=source.time_limit_seconds,
        customer_id=source.customer_id, background_asset_id=source.background_asset_id,
        shelf_asset_id=source.shelf_asset_id, preview_asset_id=source.preview_asset_id, products=list(source.products))
    db.session.add(copy)
    for order in source.orders:
        clone = CyberpunkOrder(name=order.name, display_order=order.display_order, customer_id=order.customer_id,
                               order_configuration=dict(order.order_configuration or {}))
        for item in order.items:
            clone.items.append(CyberpunkOrderItem(product_id=item.product_id, quantity=item.quantity))
        copy.orders.append(clone)
    db.session.commit()
    flash("Draft copy created.", "success")
    return redirect(url_for("cyberpunk_warehouse_admin.level_edit", level_id=copy.id))


@cyberpunk_warehouse_admin_bp.route("/levels/<int:level_id>/preview")
@login_required
@admin_required
def level_preview(level_id):
    level = db.session.get(CyberpunkLevel, level_id)
    if level is None:
        abort(404)
    products_by_id = {product.id: product for product in CyberpunkProduct.query.all()}
    return render_template("cyberpunk_warehouse_admin/level_preview.html", level=level, products_by_id=products_by_id)


@cyberpunk_warehouse_admin_bp.route("/game-modes")
@login_required
@admin_required
def game_modes():
    modes = CyberpunkGameMode.query.order_by(CyberpunkGameMode.id).all()
    return render_template("cyberpunk_warehouse_admin/game_modes.html", modes=modes)


@cyberpunk_warehouse_admin_bp.route("/sections/<section_key>")
@login_required
@admin_required
def planned_section(section_key):
    section = PLANNED_SECTIONS.get(section_key)
    if section is None:
        abort(404)
    return render_template("cyberpunk_warehouse_admin/planned.html", section_title=section[0], section_description=section[1])
