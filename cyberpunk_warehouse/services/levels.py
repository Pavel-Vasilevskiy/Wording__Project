"""Structural validation for level drafts and publication.

This deliberately does not simulate shelf accessibility or tray-state gameplay.
"""
from collections import Counter
import re

from cyberpunk_warehouse.models import CyberpunkProduct
from cyberpunk_warehouse.services.modes import validate_mode_configuration
from models import db


def level_publish_errors(level):
    errors = []
    if not isinstance(level.name, str) or not level.name.strip():
        errors.append("A level name is required.")
    if not isinstance(level.internal_key, str) or not level.internal_key.strip():
        errors.append("A unique internal key is required.")
    elif not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", level.internal_key):
        errors.append("Internal key must use lowercase letters, numbers and hyphens.")
    if level.difficulty not in {"easy", "normal", "hard", "expert"}:
        errors.append("Level difficulty is invalid.")
    if level.status not in {"draft", "published", "archived"}:
        errors.append("Level status is invalid.")
    if isinstance(level.level_number, bool) or not isinstance(level.level_number, int) or level.level_number < 1:
        errors.append("Level number must be a positive integer.")
    if level.game_mode is None or not level.game_mode.is_active:
        errors.append("An active game mode must be assigned.")
    else:
        errors.extend(validate_mode_configuration(level.game_mode.key, level.mode_configuration,
                                                 time_limit_seconds=level.time_limit_seconds))

    layout = level.warehouse_layout
    if not isinstance(layout, dict):
        errors.append("Warehouse layout must be an object.")
        layout = {}
    rows, columns = layout.get("rows"), layout.get("columns")
    valid_rows = isinstance(rows, int) and not isinstance(rows, bool) and 1 <= rows <= 8
    valid_columns = isinstance(columns, int) and not isinstance(columns, bool) and 1 <= columns <= 12
    if not valid_rows or not valid_columns:
        errors.append("Warehouse dimensions must be between 1–8 shelves and 1–12 cells per shelf.")
    safe_rows = rows if valid_rows else 0
    safe_columns = columns if valid_columns else 0
    shelves = layout.get("shelves")
    stocks = Counter()
    occupied = set()
    if not isinstance(shelves, list):
        errors.append("Warehouse shelves must be a list.")
        shelves = []
    if valid_rows and len(shelves) != safe_rows:
        errors.append("Warehouse shelf count must match the configured row count.")
    shelf_ids = [shelf.get("id") for shelf in shelves if isinstance(shelf, dict)]
    if len(shelf_ids) != len(set(shelf_ids)):
        errors.append("Warehouse shelf identifiers must be unique.")
    for shelf_index, shelf in enumerate(shelves):
        if not isinstance(shelf, dict) or not isinstance(shelf.get("cells"), list):
            errors.append("Each shelf must contain a cells list.")
            continue
        if valid_columns and len(shelf["cells"]) != safe_columns:
            errors.append("Each shelf must define every configured cell, including empty cells.")
        for cell in shelf["cells"]:
            if not isinstance(cell, dict):
                errors.append("Each warehouse cell must be an object.")
                continue
            coordinate = (cell.get("row"), cell.get("column"))
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in coordinate):
                errors.append("Warehouse layout contains an invalid cell coordinate.")
                continue
            if coordinate in occupied:
                errors.append("Warehouse layout contains duplicate cell coordinates.")
            occupied.add(coordinate)
            row, column = coordinate
            if not (0 <= row < safe_rows and 0 <= column < safe_columns) or row != shelf_index:
                errors.append("Warehouse layout contains an invalid cell coordinate.")
            product_id = cell.get("product_id")
            quantity = cell.get("quantity", 0)
            if product_id is None:
                if quantity not in (None, 0, ""):
                    errors.append("Empty cells cannot have a product quantity.")
                continue
            if isinstance(product_id, bool) or not isinstance(product_id, int):
                errors.append("Warehouse layout contains an invalid product reference.")
                continue
            product = db_get_product(product_id)
            if product is None or not product.is_active:
                errors.append("Warehouse layout references a missing or inactive product.")
                continue
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or quantity % 3:
                errors.append("Warehouse product quantities must be positive multiples of three.")
            else:
                stocks[product.id] += quantity

    assigned_ids = set(stocks)
    if not assigned_ids:
        errors.append("At least one active product must be placed in the warehouse.")
    orders = level.orders
    if not orders:
        errors.append("At least one order with products is required before publishing.")
    requested = Counter()
    for order in orders:
        if not order.items:
            errors.append(f"Order {order.display_order} must contain at least one product.")
        customer = order.customer or level.customer
        if customer is None or not customer.is_active or not customer.character_artwork_id:
            errors.append(f"Order {order.display_order} requires an active customer with default artwork.")
        for item in order.items:
            if item.product is None or not item.product.is_active:
                errors.append(f"Order {order.display_order} contains a missing or inactive product.")
            elif item.product_id not in assigned_ids:
                errors.append(f"Order {order.display_order} requests a product not present in the warehouse.")
            if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0 or item.quantity % 3:
                errors.append(f"Order {order.display_order} quantities must be positive multiples of three.")
            else:
                requested[item.product_id] += item.quantity
    for product_id, quantity in requested.items():
        if quantity > stocks[product_id]:
            errors.append(f"Requested quantity exceeds warehouse stock for product #{product_id}.")
    return list(dict.fromkeys(errors))


def db_get_product(product_id):
    try:
        return db.session.get(CyberpunkProduct, int(product_id))
    except (TypeError, ValueError):
        return None


def validate_level_for_publish(level):
    errors = level_publish_errors(level)
    if errors:
        raise ValueError(" ".join(errors))
    return True
