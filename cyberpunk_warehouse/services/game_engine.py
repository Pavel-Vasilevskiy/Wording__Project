"""Server-authoritative game state operations for Cyberpunk Warehouse.

Mode-specific entry points live behind the small registry at the bottom. The
Classic implementation uses slot-local matching; it never combines stock from
different cells.
"""
from copy import deepcopy
from datetime import datetime, timedelta


class GameActionError(ValueError):
    pass


class ClassicGameEngine:
    key = "classic"

    def initial_state(self, level_snapshot, now=None):
        now = now or datetime.utcnow()
        warehouse = deepcopy(level_snapshot["warehouse_layout"])
        orders = deepcopy(level_snapshot["orders"])
        remaining = {str(item["product_id"]): int(item["quantity"]) for item in orders[0]["items"]}
        game_seconds = level_snapshot.get("time_limit_seconds")
        timers = {
            "level_deadline_at": _iso(now + timedelta(seconds=game_seconds)) if game_seconds else None,
            "customer_deadline_at": _customer_deadline(orders[0], level_snapshot, now),
            "paused_at": None,
            "game_over_reason": None,
        }
        return {
            "version": 1,
            "level": deepcopy(level_snapshot),
            "warehouse": warehouse,
            "inventory": {},
            "order_remaining": remaining,
            "order_index": 0,
            "timers": timers,
            "moves": 0,
            "status": "active",
        }

    def apply(self, state, action, now=None):
        """Apply one validated action to a copy and return its new state."""
        now = now or datetime.utcnow()
        result = deepcopy(state)
        action_type = action.get("type") if isinstance(action, dict) else None
        if result["status"] not in {"active", "paused"}:
            raise GameActionError("This game session has already ended.")
        if result["status"] == "active" and self._expire(result, now):
            return result
        if action_type == "pause":
            if result["status"] != "active":
                raise GameActionError("The game is already paused.")
            result["status"] = "paused"
            result["timers"]["paused_at"] = _iso(now)
            return result
        if action_type == "resume":
            if result["status"] != "paused":
                raise GameActionError("The game is not paused.")
            paused_at = _parse_iso(result["timers"].get("paused_at")) or now
            pause_duration = max(0, int((now - paused_at).total_seconds()))
            for key in ("level_deadline_at", "customer_deadline_at"):
                deadline = _parse_iso(result["timers"].get(key))
                if deadline:
                    result["timers"][key] = _iso(deadline + timedelta(seconds=pause_duration))
            result["timers"]["paused_at"] = None
            result["status"] = "active"
            return result
        if result["status"] != "active":
            raise GameActionError("Resume the game before making a move.")
        if action_type == "move":
            return self._move(result, action)
        if action_type == "deliver":
            return self._deliver(result, action, now)
        if action_type == "tick":
            self._expire(result, now)
            return result
        raise GameActionError("Unknown game action.")

    def _move(self, state, action):
        source = _slot_coordinates(action.get("source"))
        target = _slot_coordinates(action.get("target"))
        if source is None or target is None:
            raise GameActionError("Select valid source and destination slots.")
        if source == target:
            return state
        source_slot = _get_slot(state["warehouse"], source)
        target_slot = _get_slot(state["warehouse"], target)
        if source_slot is None:
            raise GameActionError("The source slot is empty.")
        source_cell = _get_cell(state["warehouse"], source)
        target_cell = _get_cell(state["warehouse"], target)
        source_cell["slots"][source[2]], target_cell["slots"][target[2]] = target_slot, source_slot
        state["moves"] += 1
        self._collect_triple(state, source_cell)
        if target_cell is not source_cell:
            self._collect_triple(state, target_cell)
        return state

    def _collect_triple(self, state, cell):
        slots = cell["slots"]
        if len(slots) == 3 and slots[0] is not None and slots[0] == slots[1] == slots[2]:
            product_key = str(slots[0])
            state["inventory"][product_key] = state["inventory"].get(product_key, 0) + 3
            cell["slots"] = [None, None, None]

    def _deliver(self, state, action, now):
        product_id = action.get("product_id")
        quantity = action.get("quantity")
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id < 1:
            raise GameActionError("Select a valid product.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise GameActionError("Delivery quantity must be a positive whole number.")
        key = str(product_id)
        available = state["inventory"].get(key, 0)
        requested = state["order_remaining"].get(key, 0)
        if quantity > available:
            raise GameActionError("You do not have enough of this product in your inventory.")
        if quantity > requested:
            raise GameActionError("That is more than the customer still needs.")
        state["inventory"][key] = available - quantity
        if state["inventory"][key] == 0:
            del state["inventory"][key]
        state["order_remaining"][key] = requested - quantity
        state["moves"] += 1
        if all(amount == 0 for amount in state["order_remaining"].values()):
            next_index = state["order_index"] + 1
            if next_index >= len(state["level"]["orders"]):
                state["status"] = "completed"
            else:
                state["order_index"] = next_index
                state["timers"]["customer_deadline_at"] = _customer_deadline(
                    state["level"]["orders"][next_index], state["level"], now
                )
                state["order_remaining"] = {
                    str(item["product_id"]): int(item["quantity"])
                    for item in state["level"]["orders"][next_index]["items"]
                }
        return state

    def _expire(self, state, now):
        timers = state.get("timers") or {}
        for key, reason in (("level_deadline_at", "level_time_expired"), ("customer_deadline_at", "customer_wait_expired")):
            deadline = _parse_iso(timers.get(key))
            if deadline is not None and now >= deadline:
                state["status"] = "game_over"
                timers["game_over_reason"] = reason
                return True
        return False


class _UnavailableModeEngine:
    def __init__(self, key):
        self.key = key

    def initial_state(self, level_snapshot):
        raise GameActionError(f"The {self.key.title()} mode is not playable yet.")

    def apply(self, state, action):
        raise GameActionError(f"The {self.key.title()} mode is not playable yet.")


MODE_ENGINES = {
    "classic": ClassicGameEngine(),
    "timed": _UnavailableModeEngine("timed"),
    "unlimited": _UnavailableModeEngine("unlimited"),
    "pvp": _UnavailableModeEngine("pvp"),
}


def get_game_engine(mode_key):
    return MODE_ENGINES.get(mode_key)


def _iso(value):
    return value.replace(microsecond=0).isoformat() + "Z"


def _parse_iso(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None


def _customer_deadline(order, level, now):
    customer = order.get("customer") or {}
    configured = (level.get("gameplay_configuration") or {}).get("customer_waiting_time_seconds", 0)
    seconds = configured if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0 else customer.get("waiting_seconds", 0)
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return None
    return _iso(now + timedelta(seconds=seconds))


def _slot_coordinates(value):
    if not isinstance(value, dict):
        return None
    parts = (value.get("row"), value.get("column"), value.get("slot"))
    if any(isinstance(part, bool) or not isinstance(part, int) or part < 0 for part in parts):
        return None
    return parts


def _get_cell(layout, coordinates):
    row, column, _slot = coordinates
    try:
        cell = layout["shelves"][row]["cells"][column]
    except (KeyError, IndexError, TypeError) as exc:
        raise GameActionError("The selected warehouse cell does not exist.") from exc
    if cell.get("row") != row or cell.get("column") != column or not isinstance(cell.get("slots"), list) or len(cell["slots"]) != 3:
        raise GameActionError("The selected warehouse cell is invalid.")
    return cell


def _get_slot(layout, coordinates):
    cell = _get_cell(layout, coordinates)
    slot = coordinates[2]
    if slot > 2:
        raise GameActionError("The selected product slot does not exist.")
    return cell["slots"][slot]
