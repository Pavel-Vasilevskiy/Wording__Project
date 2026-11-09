"""Structural level validation and versioned warehouse layout helpers.

Publication checks data integrity and requested stock; it does not simulate the
future game's slot selection, tray, or progression rules.
"""
from collections import Counter
import math
import re

from cyberpunk_warehouse.models import CyberpunkProduct
from cyberpunk_warehouse.services.modes import validate_mode_configuration
from models import db

LAYOUT_VERSION = 2
MAX_LAYOUT_DIMENSION = 20
MAX_LAYOUT_CELLS = 400


def normalize_warehouse_layout(layout):
    """Return a v2 layout; convert v1 per-cell quantities into concrete triples.

    Conversion is in-memory until an administrator saves. Existing database rows
    are never rewritten on read. A legacy stock count that cannot fit is rejected
    so its information cannot be silently discarded.
    """
    if not isinstance(layout, dict):
        return {"version": LAYOUT_VERSION, "rows": 10, "columns": 10, "shelves": []}
    if layout.get("version") == LAYOUT_VERSION:
        return layout

    rows, columns = layout.get("rows", 10), layout.get("columns", 10)
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 1:
        rows = 10
    if not isinstance(columns, int) or isinstance(columns, bool) or columns < 1:
        columns = 10
    columns = min(columns, MAX_LAYOUT_DIMENSION)
    rows = min(rows, MAX_LAYOUT_DIMENSION)
    cell_map = {}
    extra_triples = []
    for shelf_index, shelf in enumerate(layout.get("shelves", []) if isinstance(layout.get("shelves"), list) else []):
        if not isinstance(shelf, dict) or not isinstance(shelf.get("cells"), list):
            continue
        for cell in shelf["cells"]:
            if not isinstance(cell, dict):
                continue
            product_id, quantity = cell.get("product_id"), cell.get("quantity", 0)
            if product_id is None or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
                continue
            if quantity % 3:
                raise ValueError("This legacy level has product quantities that cannot be converted safely.")
            row = cell.get("row", shelf_index)
            column = cell.get("column", len(cell_map))
            if not isinstance(row, int) or not 0 <= row < rows:
                row = shelf_index if shelf_index < rows else None
            if not isinstance(column, int) or not 0 <= column < columns:
                column = None
            groups = quantity // 3
            if row is not None and column is not None and (row, column) not in cell_map:
                cell_map[(row, column)] = [product_id] * 3
                groups -= 1
            extra_triples.extend([[product_id] * 3 for _ in range(max(0, groups))])

    required_cells = len(cell_map) + len(extra_triples)
    if required_cells > MAX_LAYOUT_CELLS:
        raise ValueError("This legacy layout has more stock than the supported grid can preserve. It was left unchanged.")
    rows = max(rows, math.ceil(required_cells / columns) if required_cells else rows)
    if rows > MAX_LAYOUT_DIMENSION:
        raise ValueError("This legacy layout needs a larger grid than the editor currently supports. It was left unchanged.")
    for row in range(rows):
        for column in range(columns):
            if not extra_triples:
                break
            if (row, column) not in cell_map:
                cell_map[(row, column)] = extra_triples.pop(0)

    shelves = []
    for row in range(rows):
        cells = []
        for column in range(columns):
            cells.append({"row": row, "column": column,
                          "slots": list(cell_map.get((row, column), [None, None, None]))})
        shelves.append({"id": f"shelf-{row + 1}", "name": f"Shelf {row + 1}", "cells": cells})
    return {"version": LAYOUT_VERSION, "rows": rows, "columns": columns, "shelves": shelves}


def level_publish_errors(level):
    errors = []
    if not isinstance(level.name, str) or not level.name.strip():
        errors.append("A level name is required.")
    if not isinstance(level.internal_key, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", level.internal_key):
        errors.append("A valid unique internal key is required.")
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
    valid_rows = isinstance(rows, int) and not isinstance(rows, bool) and 1 <= rows <= MAX_LAYOUT_DIMENSION
    valid_columns = isinstance(columns, int) and not isinstance(columns, bool) and 1 <= columns <= MAX_LAYOUT_DIMENSION
    if not valid_rows or not valid_columns or (valid_rows and valid_columns and rows * columns > MAX_LAYOUT_CELLS):
        errors.append("Warehouse dimensions must be between 1 and 20, with at most 400 cells.")
    safe_rows, safe_columns = rows if valid_rows else 0, columns if valid_columns else 0
    version = layout.get("version", 1)
    shelves = layout.get("shelves")
    if not isinstance(shelves, list):
        errors.append("Warehouse shelves must be a list.")
        shelves = []
    if valid_rows and len(shelves) != safe_rows:
        errors.append("Warehouse shelf count must match the configured row count.")
    shelf_ids = [shelf.get("id") for shelf in shelves if isinstance(shelf, dict)]
    if len(shelf_ids) != len(set(shelf_ids)):
        errors.append("Warehouse shelf identifiers must be unique.")

    stock = Counter()
    occupied_coordinates = set()
    for shelf_index, shelf in enumerate(shelves):
        if not isinstance(shelf, dict) or not isinstance(shelf.get("cells"), list):
            errors.append("Each shelf must contain a cells list.")
            continue
        if valid_columns and len(shelf["cells"]) != safe_columns:
            errors.append("Each shelf must define every configured cell.")
        for cell in shelf["cells"]:
            if not isinstance(cell, dict):
                errors.append("Each warehouse cell must be an object.")
                continue
            row, column = cell.get("row"), cell.get("column")
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in (row, column)):
                errors.append("Warehouse layout contains an invalid cell coordinate.")
                continue
            coordinate = (row, column)
            if coordinate in occupied_coordinates:
                errors.append("Warehouse layout contains duplicate cell coordinates.")
            occupied_coordinates.add(coordinate)
            if not (0 <= row < safe_rows and 0 <= column < safe_columns) or row != shelf_index:
                errors.append("Warehouse layout contains an invalid cell coordinate.")
            if version == 1:
                product_id, quantity = cell.get("product_id"), cell.get("quantity", 0)
                if product_id is None:
                    if quantity not in (None, 0, ""):
                        errors.append("Empty legacy cells cannot have a quantity.")
                elif isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or quantity % 3:
                    errors.append("Legacy warehouse quantities must be positive multiples of three.")
                else:
                    product = _get_active_product(product_id)
                    if product is None:
                        errors.append("Warehouse layout references a missing or inactive product.")
                    else:
                        stock[product.id] += quantity
                continue
            slots = cell.get("slots")
            if not isinstance(slots, list) or len(slots) != 3:
                errors.append("Every warehouse cell must contain exactly three product slots.")
                continue
            for product_id in slots:
                if product_id is None:
                    errors.append("Published levels cannot contain empty product slots.")
                    continue
                if isinstance(product_id, bool) or not isinstance(product_id, int):
                    errors.append("Warehouse layout contains an invalid product reference.")
                    continue
                product = _get_active_product(product_id)
                if product is None:
                    errors.append("Warehouse layout references a missing or inactive product.")
                else:
                    stock[product.id] += 1
    if version not in (1, LAYOUT_VERSION):
        errors.append("Warehouse layout configuration version is unsupported.")
    if not stock:
        errors.append("At least one active product must be present in the warehouse.")

    if not level.orders:
        errors.append("At least one order with products is required before publishing.")
    requested = Counter()
    for order in level.orders:
        if not order.items:
            errors.append(f"Order {order.display_order} must contain at least one product.")
        customer = order.customer or level.customer
        if customer is None or not customer.is_active or not customer.character_artwork_id:
            errors.append(f"Order {order.display_order} requires an active customer with default artwork.")
        for item in order.items:
            if item.product is None or not item.product.is_active:
                errors.append(f"Order {order.display_order} contains a missing or inactive product.")
            elif item.product_id not in stock:
                errors.append(f"Order {order.display_order} requests a product not present in the warehouse.")
            quantity = item.quantity
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                errors.append(f"Order {order.display_order} quantities must be positive whole numbers.")
            else:
                requested[item.product_id] += quantity
    for product_id, quantity in requested.items():
        if quantity > stock[product_id]:
            errors.append(f"Requested quantity exceeds warehouse stock for product #{product_id}.")
    return list(dict.fromkeys(errors))


def _get_active_product(product_id):
    if isinstance(product_id, bool) or not isinstance(product_id, int):
        return None
    return db.session.get(CyberpunkProduct, product_id)


def validate_level_for_publish(level):
    errors = level_publish_errors(level)
    if errors:
        raise ValueError(" ".join(errors))
    return True
