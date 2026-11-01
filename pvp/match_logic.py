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
    """Close a shared Timer round and advance the whole roster exactly once."""
    if match.status != "in_progress":
        return False
    if match.game_mode != "timer":
        if all(player.status in {"left", "completed"} for player in match.players):
            match.status = "completed"
            match.completed_at = now
            return True
        return False
    current = next((item for item in match.rounds if item.round_number == match.current_round), None)
    if current is None or current.status not in {"in_progress", "expired"} or not match.players:
        return False
    terminal = {"solved", "failed", "expired", "completed"}
    active_players = [player for player in match.players if player.status != "left"]
    if any(player.current_round != current.round_number or player.status not in terminal for player in active_players):
        return False

    current.status = "completed"
    for player in active_players:
        _archive_player_round(player, current)

    if not active_players or current.round_number >= match.words_count:
        match.status = "completed"
        match.completed_at = now
        return True

    next_round = next((item for item in match.rounds if item.round_number == current.round_number + 1), None)
    if next_round is None:
        # Keep the match in a terminal state if its pre-generated sequence is corrupt.
        match.status = "completed"
        match.completed_at = now
        return True

    match.current_round = next_round.round_number
    next_round.status = "in_progress"
    next_round.started_at = now
    next_round.deadline_at = now + timedelta(seconds=match.timer_seconds) if match.timer_seconds else None
    for player in active_players:
        player.current_round = next_round.round_number
        player.attempts_used = 0
        player.has_solved = False
        player.status = "active"
        player.progress = "[]"
    return True


def advance_no_limit_player(match, player, now):
    """Advance one No time limit player immediately after a solved/failed word."""
    if match.status != "in_progress" or match.game_mode == "timer" or player.status not in {"solved", "failed"}:
        return False
    current = next((item for item in match.rounds if item.round_number == player.current_round), None)
    if current is None:
        return False
    _archive_player_round(player, current)

    if player.current_round >= match.words_count:
        player.status = "completed"
        player.current_round_started_at = None
    else:
        player.current_round += 1
        player.attempts_used = 0
        player.has_solved = False
        player.progress = "[]"
        player.status = "active"
        player.current_round_started_at = now
        next_round = next((item for item in match.rounds if item.round_number == player.current_round), None)
        if next_round is not None and next_round.status == "waiting":
            next_round.status = "in_progress"
            next_round.started_at = now
    match.current_round = max(match.current_round, player.current_round)

    # A shared round is informational in this mode; close it after all remaining
    # participants have passed it or left. Attempts remain in their own records.
    for round_ in match.rounds:
        if round_.status == "in_progress" and all(
            candidate.status == "left"
            or candidate.status == "completed"
            or candidate.current_round > round_.round_number
            for candidate in match.players
        ):
            round_.status = "completed"

    if all(candidate.status in {"left", "completed"} for candidate in match.players):
        match.status = "completed"
        match.completed_at = now
    return True


def _archive_player_round(player, round_):
    """Keep a color-only per-round summary for later history and result screens."""
    try:
        history = json.loads(player.round_history or "[]")
    except (TypeError, ValueError):
        history = []
    if not isinstance(history, list):
        history = []
    if any(isinstance(item, dict) and item.get("round_number") == round_.round_number for item in history):
        return
    attempts = sorted(
        (attempt for attempt in player.attempts if attempt.round_id == round_.id),
        key=lambda attempt: attempt.attempt_number,
    )
    history.append({
        "round_number": round_.round_number,
        "status": player.status,
        "progress": [
            colors for attempt in attempts
            if (colors := safe_color_row(attempt.colors)) is not None
        ],
    })
    player.round_history = json.dumps(history, separators=(",", ":"))


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
        if match.game_mode != "timer":
            for player in match.players:
                if player.current_round == 1 and player.status != "left":
                    player.current_round_started_at = match.scheduled_start_at
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
