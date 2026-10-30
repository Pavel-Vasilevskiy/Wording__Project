"""Small deterministic helpers for shared match timing and safe public progress."""

import json
import math
import secrets
from collections import Counter
from datetime import timedelta


COUNTDOWN_SECONDS = 5
PUBLIC_COLORS = {"green", "yellow", "gray"}


def choose_word_sequence(dictionary, count):
    """Select unique shared answers from the already validated local dictionary."""
    if count < 1 or count > len(dictionary):
        raise ValueError("Dictionary cannot supply the requested number of unique words")
    return secrets.SystemRandom().sample(dictionary.words, count)


def evaluate_guess(guess, answer):
    """Return Wordle colors, accounting for repeated letters exactly once per answer occurrence."""
    colors = [None] * 5
    remaining = Counter(answer)
    for index, letter in enumerate(guess):
        if letter == answer[index]:
            colors[index] = "green"
            remaining[letter] -= 1
    for index, letter in enumerate(guess):
        if colors[index] is not None:
            continue
        if remaining[letter] > 0:
            colors[index] = "yellow"
            remaining[letter] -= 1
        else:
            colors[index] = "gray"
    return colors


def expire_unfinished_players(match):
    """Mark players still active after the shared round deadline as expired."""
    if match.status != "in_progress":
        return False
    current = next((item for item in match.rounds if item.round_number == match.current_round), None)
    if current is None or current.status != "expired":
        return False
    changed = False
    for player in match.players:
        if player.current_round == current.round_number and player.status == "active":
            player.status = "expired"
            changed = True
    return changed


def complete_round_if_all_players_finished(match, now):
    """Close the first/current round once every rostered player is terminal."""
    if match.status != "in_progress":
        return False
    current = next((item for item in match.rounds if item.round_number == match.current_round), None)
    if current is None or current.status != "in_progress" or not match.players:
        return False
    terminal = {"solved", "failed", "expired", "left"}
    if all(player.current_round == current.round_number and player.status in terminal for player in match.players):
        current.status = "completed"
        return True
    return False


def advance_match_clock(match, now):
    """Materialize due transitions; callers must hold the database write lock."""
    changed = False
    if match.status == "countdown" and match.scheduled_start_at <= now:
        match.status = "in_progress"
        # Use the persisted scheduled instant so every client gets the same timer.
        match.started_at = match.scheduled_start_at
        first_round = next((item for item in match.rounds if item.round_number == 1), None)
        if first_round is not None:
            first_round.status = "in_progress"
            first_round.started_at = match.scheduled_start_at
            first_round.deadline_at = (
                match.scheduled_start_at + timedelta(seconds=match.timer_seconds)
                if match.game_mode == "timer" else None
            )
        changed = True

    if match.status == "in_progress":
        current = next((item for item in match.rounds if item.round_number == match.current_round), None)
        if current and current.status == "in_progress" and current.deadline_at and current.deadline_at <= now:
            current.status = "expired"
            changed = True
    return changed


def safe_progress_rows(raw_progress):
    """Parse only rows of five color names; never expose answer or guess text."""
    try:
        parsed = json.loads(raw_progress or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    safe_rows = []
    for row in parsed:
        if not isinstance(row, list) or len(row) != 5:
            continue
        if all(isinstance(color, str) and color in PUBLIC_COLORS for color in row):
            safe_rows.append(list(row))
    return safe_rows


def safe_color_row(raw_colors):
    """Parse one persisted result row and reject all non-color data."""
    try:
        parsed = json.loads(raw_colors or "[]") if isinstance(raw_colors, str) else raw_colors
    except (TypeError, ValueError):
        return None
    if (
        isinstance(parsed, list)
        and len(parsed) == 5
        and all(isinstance(color, str) and color in PUBLIC_COLORS for color in parsed)
    ):
        return parsed
    return None


def seconds_remaining(deadline_at, now):
    if deadline_at is None:
        return None
    return max(0, math.ceil((deadline_at - now).total_seconds()))
