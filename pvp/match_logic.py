"""Small deterministic helpers for shared match timing and safe public progress."""

import json
import math
import secrets
from datetime import timedelta


COUNTDOWN_SECONDS = 5
PUBLIC_COLORS = {"green", "yellow", "gray"}


def choose_word_sequence(dictionary, count):
    """Select unique shared answers from the already validated local dictionary."""
    if count < 1 or count > len(dictionary):
        raise ValueError("Dictionary cannot supply the requested number of unique words")
    return secrets.SystemRandom().sample(dictionary.words, count)


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
        if all(color in PUBLIC_COLORS for color in row):
            safe_rows.append(list(row))
    return safe_rows


def seconds_remaining(deadline_at, now):
    if deadline_at is None:
        return None
    return max(0, math.ceil((deadline_at - now).total_seconds()))
