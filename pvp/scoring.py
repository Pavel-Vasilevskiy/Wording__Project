"""Validated PvP balance configuration and server-owned scoring rules."""

import json
from pathlib import Path


class BalanceConfigError(RuntimeError):
    """Raised when the required PvP balance file is missing or invalid."""


BALANCE_PATH = Path(__file__).with_name("balance.json")
COLOR_RANK = {"gray": 0, "yellow": 1, "green": 2}


def load_balance_config(path=None):
    """Load and strictly validate the PvP balance JSON; never silently default."""
    config_path = Path(path) if path is not None else BALANCE_PATH
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BalanceConfigError(f"Cannot read PvP balance file '{config_path}': {exc}") from exc
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BalanceConfigError(f"PvP balance file '{config_path}' is not valid UTF-8 JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise BalanceConfigError("PvP balance must be a JSON object")
    expected_keys = {
        "version", "letter_points", "word_solved_bonus", "unused_attempt_bonus",
        "time_bonus", "no_time_limit_time_bonus",
    }
    if set(data) != expected_keys:
        raise BalanceConfigError(f"PvP balance must contain exactly: {', '.join(sorted(expected_keys))}")
    if not _is_nonnegative_integer(data["version"]) or data["version"] < 1:
        raise BalanceConfigError("PvP balance version must be a positive integer")

    letter_points = data["letter_points"]
    if not isinstance(letter_points, dict) or set(letter_points) != set(COLOR_RANK):
        raise BalanceConfigError("letter_points must define exactly gray, yellow, and green")
    if any(not _is_nonnegative_integer(value) for value in letter_points.values()):
        raise BalanceConfigError("letter_points values must be nonnegative integers")
    if not (letter_points["gray"] <= letter_points["yellow"] <= letter_points["green"]):
        raise BalanceConfigError("letter_points must increase or stay equal from gray to yellow to green")

    for key in ("word_solved_bonus", "unused_attempt_bonus"):
        if not _is_nonnegative_integer(data[key]):
            raise BalanceConfigError(f"{key} must be a nonnegative integer")

    time_bonus = data["time_bonus"]
    if not isinstance(time_bonus, dict) or set(time_bonus) != {"enabled", "max_points"}:
        raise BalanceConfigError("time_bonus must contain exactly enabled and max_points")
    if not isinstance(time_bonus["enabled"], bool):
        raise BalanceConfigError("time_bonus.enabled must be a boolean")
    if not _is_nonnegative_integer(time_bonus["max_points"]):
        raise BalanceConfigError("time_bonus.max_points must be a nonnegative integer")

    no_limit_bonus = data["no_time_limit_time_bonus"]
    if not isinstance(no_limit_bonus, dict) or set(no_limit_bonus) != {"enabled", "tiers"}:
        raise BalanceConfigError("no_time_limit_time_bonus must contain exactly enabled and tiers")
    if not isinstance(no_limit_bonus["enabled"], bool):
        raise BalanceConfigError("no_time_limit_time_bonus.enabled must be a boolean")
    tiers = no_limit_bonus["tiers"]
    if not isinstance(tiers, list) or not tiers:
        raise BalanceConfigError("no_time_limit_time_bonus.tiers must be a nonempty list")
    validated_tiers = []
    previous_threshold = 0
    previous_points = None
    for tier in tiers:
        if not isinstance(tier, dict) or set(tier) != {"under_seconds", "points"}:
            raise BalanceConfigError("Each no-limit time bonus tier must define under_seconds and points")
        threshold = tier["under_seconds"]
        tier_points = tier["points"]
        if not _is_nonnegative_integer(threshold) or threshold <= previous_threshold:
            raise BalanceConfigError("No-limit time thresholds must be positive and strictly increasing")
        if not _is_nonnegative_integer(tier_points):
            raise BalanceConfigError("No-limit time tier points must be nonnegative integers")
        if previous_points is not None and tier_points > previous_points:
            raise BalanceConfigError("No-limit time bonus points must not increase at slower tiers")
        validated_tiers.append({"under_seconds": threshold, "points": tier_points})
        previous_threshold = threshold
        previous_points = tier_points

    # Return a detached plain structure so request code cannot mutate shared config.
    return {
        "version": data["version"],
        "letter_points": dict(letter_points),
        "word_solved_bonus": data["word_solved_bonus"],
        "unused_attempt_bonus": data["unused_attempt_bonus"],
        "time_bonus": dict(time_bonus),
        "no_time_limit_time_bonus": {
            "enabled": no_limit_bonus["enabled"],
            "tiers": validated_tiers,
        },
    }


def _is_nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def score_new_letter_information(previous_attempts, guess, colors, balance):
    """Award only first or improved feedback for each distinct letter in a word."""
    best_known = {}
    points = balance["letter_points"]
    for attempt in previous_attempts:
        prior_guess = _attempt_value(attempt, "guess")
        prior_colors = _attempt_colors(attempt)
        for index, (letter, color) in enumerate(zip(prior_guess, prior_colors)):
            if letter.isalpha() and color in COLOR_RANK:
                key = letter.upper()
                current = best_known.get(key)
                if current is None or COLOR_RANK[color] > COLOR_RANK[current]:
                    best_known[key] = color

    best_this_attempt = {}
    for index, (letter, color) in enumerate(zip(guess, colors)):
        key = letter.upper()
        current = best_this_attempt.get(key)
        if color in COLOR_RANK and (current is None or COLOR_RANK[color] > COLOR_RANK[current[0]]):
            best_this_attempt[key] = (color, index)

    total = 0
    awards = []
    for key, (color, index) in best_this_attempt.items():
        old_color = best_known.get(key)
        if old_color is not None and COLOR_RANK[color] <= COLOR_RANK[old_color]:
            continue
        earned = points[color] if old_color is None else max(0, points[color] - points[old_color])
        awards.append({
            "position": index + 1,
            "letter": key,
            "from": old_color,
            "to": color,
            "points": earned,
        })
        total += earned
        best_known[key] = color
    return total, awards


def calculate_attempt_score(
    previous_attempts,
    guess,
    colors,
    balance,
    *,
    solved,
    attempt_number,
    attempts_limit,
    game_mode,
    now,
    deadline_at,
    timer_seconds,
    word_elapsed_seconds=None,
):
    """Build the immutable score contribution for one accepted server-checked guess."""
    letter_total, letter_awards = score_new_letter_information(
        previous_attempts, guess, colors, balance
    )
    solved_bonus = balance["word_solved_bonus"] if solved else 0
    unused_bonus = (
        max(0, attempts_limit - attempt_number) * balance["unused_attempt_bonus"]
        if solved else 0
    )
    time_bonus = 0
    if (
        solved
        and game_mode == "timer"
        and balance["time_bonus"]["enabled"]
        and deadline_at is not None
        and isinstance(timer_seconds, int)
        and timer_seconds > 0
    ):
        remaining_seconds = max(0.0, min(float(timer_seconds), (deadline_at - now).total_seconds()))
        time_bonus = round(balance["time_bonus"]["max_points"] * remaining_seconds / timer_seconds)
    elif (
        solved
        and game_mode != "timer"
        and balance["no_time_limit_time_bonus"]["enabled"]
        and isinstance(word_elapsed_seconds, (int, float))
        and not isinstance(word_elapsed_seconds, bool)
        and word_elapsed_seconds >= 0
    ):
        for tier in balance["no_time_limit_time_bonus"]["tiers"]:
            if word_elapsed_seconds < tier["under_seconds"]:
                time_bonus = tier["points"]
                break

    breakdown = {
        "balance_version": balance["version"],
        "letter_points": letter_total,
        "letter_awards": letter_awards,
        "word_solved_bonus": solved_bonus,
        "unused_attempt_bonus": unused_bonus,
        "time_bonus": time_bonus,
        "word_elapsed_seconds": (
            round(float(word_elapsed_seconds), 3)
            if game_mode != "timer"
            and isinstance(word_elapsed_seconds, (int, float))
            and not isinstance(word_elapsed_seconds, bool)
            and word_elapsed_seconds >= 0
            else None
        ),
    }
    breakdown["total"] = sum((letter_total, solved_bonus, unused_bonus, time_bonus))
    return breakdown


def _attempt_value(attempt, field):
    if isinstance(attempt, dict):
        value = attempt.get(field, "")
    else:
        value = getattr(attempt, field, "")
    return value if isinstance(value, str) else ""


def _attempt_colors(attempt):
    raw_colors = attempt.get("colors", []) if isinstance(attempt, dict) else getattr(attempt, "colors", "[]")
    try:
        colors = json.loads(raw_colors) if isinstance(raw_colors, str) else raw_colors
    except (TypeError, ValueError):
        return []
    return colors if isinstance(colors, list) else []
