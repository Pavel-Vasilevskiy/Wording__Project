import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from pvp.scoring import (
    BalanceConfigError,
    calculate_attempt_score,
    load_balance_config,
    score_new_letter_information,
)


def balance_config():
    return {
        "version": 1,
        "letter_points": {"gray": 0, "yellow": 5, "green": 10},
        "word_solved_bonus": 15,
        "unused_attempt_bonus": 12,
        "time_bonus": {"enabled": True, "max_points": 20},
        "no_time_limit_time_bonus": {
            "enabled": True,
            "tiers": [
                {"under_seconds": 30, "points": 100},
                {"under_seconds": 60, "points": 60},
                {"under_seconds": 90, "points": 30},
                {"under_seconds": 120, "points": 10},
            ],
        },
    }


class PvPScoringTests(unittest.TestCase):
    def setUp(self):
        self.balance_path = Path(__file__).parent / ".pvp-balance-test.json"

    def tearDown(self):
        self.balance_path.unlink(missing_ok=True)

    def write_config(self, value):
        path = self.balance_path
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def test_balance_json_loads_validated_values(self):
        expected = balance_config()
        actual = load_balance_config(self.write_config(expected))
        self.assertEqual(actual, expected)

    def test_missing_balance_file_raises_clear_error(self):
        with self.assertRaisesRegex(BalanceConfigError, "Cannot read PvP balance"):
            load_balance_config(self.balance_path.with_name(".missing-pvp-balance.json"))

    def test_invalid_json_raises_clear_error(self):
        with self.assertRaisesRegex(BalanceConfigError, "not valid UTF-8 JSON"):
            load_balance_config(self.write_config("{not json"))

    def test_invalid_balance_shape_and_types_are_rejected(self):
        invalid_configs = []
        missing_key = balance_config()
        del missing_key["word_solved_bonus"]
        invalid_configs.append(missing_key)
        bad_points = balance_config()
        bad_points["letter_points"]["green"] = True
        invalid_configs.append(bad_points)
        bad_order = balance_config()
        bad_order["letter_points"] = {"gray": 5, "yellow": 1, "green": 10}
        invalid_configs.append(bad_order)
        bad_flag = balance_config()
        bad_flag["time_bonus"]["enabled"] = 1
        invalid_configs.append(bad_flag)

        for invalid in invalid_configs:
            with self.subTest(invalid=invalid):
                with self.assertRaises(BalanceConfigError):
                    load_balance_config(self.write_config(invalid))

    def test_new_gray_yellow_and_green_results_award_configured_points_once(self):
        guess = "CIGAR"
        colors = ["gray", "yellow", "green", "gray", "gray"]
        earned, awards = score_new_letter_information([], guess, colors, balance_config())
        self.assertEqual(earned, 15)
        self.assertEqual([award["points"] for award in awards], [0, 5, 10, 0, 0])

        repeated, repeated_awards = score_new_letter_information(
            [{"guess": guess, "colors": colors}], guess, colors, balance_config()
        )
        self.assertEqual(repeated, 0)
        self.assertEqual(repeated_awards, [])

    def test_better_feedback_awards_only_the_difference(self):
        previous = [{"guess": "CIGAR", "colors": ["gray", "gray", "green", "gray", "gray"]}]
        earned, awards = score_new_letter_information(
            previous, "CIGAR", ["green", "yellow", "green", "gray", "gray"], balance_config()
        )
        self.assertEqual(earned, 15)  # C gray->green: 10; I gray->yellow: 5.
        self.assertEqual([item["points"] for item in awards], [10, 5])

    def test_repeated_letters_are_tracked_once_using_the_best_feedback(self):
        guess = "ARRAY"
        colors = ["gray", "yellow", "gray", "green", "gray"]
        first, _ = score_new_letter_information([], guess, colors, balance_config())
        repeated, _ = score_new_letter_information(
            [{"guess": guess, "colors": colors}], guess, colors, balance_config()
        )
        # Repeated A tiles yield only its strongest new feedback once.
        self.assertEqual(first, 15)
        self.assertEqual(repeated, 0)

    def test_solve_bonuses_and_timer_bonus_use_server_timestamps(self):
        now = datetime(2026, 1, 1, 12, 1)
        result = calculate_attempt_score(
            [], "CIGAR", ["green"] * 5, balance_config(),
            solved=True, attempt_number=3, attempts_limit=6, game_mode="timer",
            now=now, deadline_at=now + timedelta(seconds=60), timer_seconds=120,
        )
        self.assertEqual(result["word_solved_bonus"], 15)
        self.assertEqual(result["unused_attempt_bonus"], 36)
        self.assertEqual(result["time_bonus"], 10)
        self.assertEqual(result["total"], 111)

    def test_unsolved_and_no_limit_guesses_do_not_get_solve_or_time_bonuses(self):
        now = datetime(2026, 1, 1, 12, 1)
        for mode in ("timer", "no_limit"):
            result = calculate_attempt_score(
                [], "ARRAY", ["gray"] * 5, balance_config(),
                solved=False, attempt_number=2, attempts_limit=6, game_mode=mode,
                now=now, deadline_at=now + timedelta(seconds=60), timer_seconds=120,
            )
            self.assertEqual(result["word_solved_bonus"], 0)
            self.assertEqual(result["unused_attempt_bonus"], 0)
            self.assertEqual(result["time_bonus"], 0)

        no_limit_solved = calculate_attempt_score(
            [], "CIGAR", ["green"] * 5, balance_config(),
            solved=True, attempt_number=1, attempts_limit=6, game_mode="no_limit",
            now=now, deadline_at=None, timer_seconds=None,
        )
        self.assertEqual(no_limit_solved["time_bonus"], 0)

    def test_time_bonus_is_clamped_to_zero_after_deadline(self):
        now = datetime(2026, 1, 1, 12, 2)
        result = calculate_attempt_score(
            [], "CIGAR", ["green"] * 5, balance_config(),
            solved=True, attempt_number=1, attempts_limit=6, game_mode="timer",
            now=now, deadline_at=now - timedelta(seconds=10), timer_seconds=120,
        )
        self.assertEqual(result["time_bonus"], 0)

    def test_no_limit_time_bonus_has_exact_under_threshold_boundaries(self):
        expected = {
            0: 100,
            29.999: 100,
            30: 60,
            59.999: 60,
            60: 30,
            89.999: 30,
            90: 10,
            119.999: 10,
            120: 0,
            900: 0,
        }
        for elapsed, bonus in expected.items():
            with self.subTest(elapsed=elapsed):
                result = calculate_attempt_score(
                    [], "CIGAR", ["green"] * 5, balance_config(),
                    solved=True, attempt_number=1, attempts_limit=6, game_mode="no_limit",
                    now=datetime(2026, 1, 1), deadline_at=None, timer_seconds=None,
                    word_elapsed_seconds=elapsed,
                )
                self.assertEqual(result["time_bonus"], bonus)

    def test_gray_zero_points_remain_a_valid_configured_category(self):
        config = balance_config()
        config["letter_points"]["gray"] = 0
        self.assertEqual(load_balance_config(self.write_config(config))["letter_points"]["gray"], 0)
        score, awards = score_new_letter_information(
            [], "CRANE", ["gray"] * 5, config
        )
        self.assertEqual(score, 0)
        self.assertTrue(all(award["points"] == 0 for award in awards))


if __name__ == "__main__":
    unittest.main()
