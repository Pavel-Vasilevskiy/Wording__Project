"""Atomic, idempotent PvP result snapshots and lifetime statistics."""

import json

from models import db
from pvp.models import PvPMatchPlayer, PvPMatchResult, PvPPlayerStats


def _attempt_breakdown(attempt):
    try:
        value = json.loads(attempt.score_breakdown or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _nonnegative_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _player_snapshot(player):
    categories = {
        "green_points": 0,
        "yellow_points": 0,
        "time_bonus": 0,
        "word_solved_bonus": 0,
        "unused_attempt_bonus": 0,
    }
    solved_rounds = set()
    for attempt in player.attempts:
        breakdown = _attempt_breakdown(attempt)
        for award in breakdown.get("letter_awards", []):
            if not isinstance(award, dict):
                continue
            color = award.get("to")
            if color in ("green", "yellow"):
                categories[f"{color}_points"] += _nonnegative_int(award.get("points"))
        for key in ("time_bonus", "word_solved_bonus", "unused_attempt_bonus"):
            categories[key] += _nonnegative_int(breakdown.get(key))
        if attempt.is_correct:
            solved_rounds.add(attempt.round_id)

    try:
        history = json.loads(player.round_history or "[]")
    except (TypeError, ValueError):
        history = []
    completed_rounds = {
        item.get("round_number")
        for item in history
        if isinstance(item, dict)
        and isinstance(item.get("round_number"), int)
        and item.get("status") in {"solved", "failed", "expired"}
    } if isinstance(history, list) else set()

    return {
        **categories,
        "words_solved": len(solved_rounds),
        "total_attempts": len(player.attempts),
        "completed_words": len(completed_rounds),
    }


def persist_completed_match_results(match, finalized_at=None):
    """Persist result and stats rows in the caller's active transaction.

    Callers must hold the existing SQLite BEGIN IMMEDIATE write lock. Each
    result row is the idempotency ledger for its corresponding stats update.
    """
    if match.status != "completed":
        return False

    finalized_at = finalized_at or match.completed_at
    existing_user_ids = {
        user_id for (user_id,) in db.session.query(PvPMatchResult.user_id)
        .filter_by(match_id=match.id).all()
    }
    all_players = list(match.players)
    participants = [player for player in all_players if player.user_id not in existing_user_ids]
    if not participants:
        return False

    eligible = sorted(
        (player for player in all_players if player.status != "left"),
        key=lambda player: (-player.score, player.joined_at, player.user_id),
    )
    winning_score = eligible[0].score if eligible else None
    places = {}
    previous_score = None
    current_place = None
    for index, player in enumerate(eligible, start=1):
        if previous_score is None or player.score != previous_score:
            current_place = index
            previous_score = player.score
        places[player.user_id] = current_place

    for player in participants:
        details = _player_snapshot(player)
        is_eligible = player.status != "left"
        is_winner = bool(is_eligible and winning_score is not None and player.score == winning_score)
        participation_status = "left" if player.status == "left" else "completed"
        result = PvPMatchResult(
            match_id=match.id,
            user_id=player.user_id,
            game_mode=match.game_mode,
            words_count=match.words_count,
            final_score=player.score,
            place=places.get(player.user_id),
            is_winner=is_winner,
            words_solved=details["words_solved"],
            total_attempts=details["total_attempts"],
            completed_words=details["completed_words"],
            green_points=details["green_points"],
            yellow_points=details["yellow_points"],
            time_bonus=details["time_bonus"],
            word_solved_bonus=details["word_solved_bonus"],
            unused_attempt_bonus=details["unused_attempt_bonus"],
            participation_status=participation_status,
            score_breakdown=json.dumps(details, separators=(",", ":")),
            finalized_at=finalized_at,
        )
        db.session.add(result)
        db.session.flush()

        stats = db.session.get(PvPPlayerStats, player.user_id)
        if stats is None:
            stats = PvPPlayerStats(
                user_id=player.user_id,
                matches_played=0,
                wins=0,
                total_score=0,
                words_solved=0,
                total_attempts=0,
                solved_words_count=0,
                best_match_score=0,
            )
            db.session.add(stats)
        stats.matches_played += 1
        stats.wins += int(is_winner)
        stats.total_score += player.score
        stats.words_solved += details["words_solved"]
        stats.total_attempts += details["total_attempts"]
        stats.solved_words_count += details["words_solved"]
        stats.best_match_score = max(stats.best_match_score, player.score)
        stats.updated_at = finalized_at

    return True
