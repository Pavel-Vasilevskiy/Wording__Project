import random
import unittest
from pathlib import Path

from word_dictionary import (
    DictionaryLoadError,
    EN_US_DICTIONARY,
    WordDictionary,
    get_dictionary_stats,
    get_random_answer,
    get_random_answers,
    is_allowed_answer,
    is_valid_guess,
    load_dictionary,
    validate_guess,
)


class WordDictionaryV2Tests(unittest.TestCase):
    def test_common_and_less_common_words_are_allowed_but_answers_are_separate(self):
        self.assertTrue(is_valid_guess("CRANE", "en", 5))
        self.assertTrue(is_valid_guess("craal", "en-US", 5))
        self.assertFalse(is_allowed_answer("craal", "en", 5))
        self.assertFalse(is_valid_guess("qzxjk", "en", 5))

    def test_normalization_length_and_ascii_rules(self):
        self.assertTrue(is_valid_guess("crane", "en", 5))
        self.assertTrue(is_valid_guess("CRANE", "en", 5))
        for value, length in (
            ("crane", 4), ("craner", 5), ("crane ", 5), ("cra ne", 5),
            ("cra1e", 5), ("crane!", 5), ("éabcd", 5), ("Krane", 5),
        ):
            with self.subTest(value=value, length=length):
                self.assertFalse(is_valid_guess(value, "en", length))

    def test_blacklist_is_exact_word_only_and_applies_to_guesses_and_answers(self):
        for blocked in EN_US_DICTIONARY.blocked_words:
            self.assertFalse(EN_US_DICTIONARY.is_valid_guess(blocked, len(blocked)))
            self.assertFalse(EN_US_DICTIONARY.is_allowed_answer(blocked, len(blocked)))
        self.assertFalse(is_valid_guess("bitch", "en", 5))
        self.assertFalse(is_valid_guess("chink", "en", 5))
        self.assertTrue(is_valid_guess("class", "en", 5))
        self.assertTrue(is_valid_guess("assay", "en", 5))

    def test_answer_set_is_clean_subset_of_allowed_guesses(self):
        allowed = set(EN_US_DICTIONARY.allowed_words(5))
        answers = EN_US_DICTIONARY.answer_words(5)
        self.assertGreater(len(allowed), 8000)
        self.assertGreater(len(answers), 3000)
        self.assertLess(len(answers), 4000)
        self.assertEqual(len(answers), len(set(answers)))
        self.assertTrue(set(answers).issubset(allowed))
        self.assertFalse(set(answers).intersection(EN_US_DICTIONARY.blocked_words))
        self.assertTrue(all(word.isascii() and word.isalpha() and len(word) == 5 for word in answers))

    def test_answer_selection_uses_answer_set_and_controlled_random_source(self):
        rng = random.Random(7)
        answers = get_random_answers(100, "en", 5, random_source=rng)
        self.assertEqual(len(answers), len(set(answers)))
        self.assertTrue(all(is_allowed_answer(word, "en", 5) for word in answers))
        deterministic = random.Random(13)
        first = get_random_answer("en-US", 5, random_source=deterministic)
        self.assertIn(first, EN_US_DICTIONARY.answer_words(5))
        self.assertNotEqual(first, get_random_answer("en", 5, exclude=(first,)))

    def test_dictionary_stats_cover_language_and_each_configured_length(self):
        stats = get_dictionary_stats("en")
        self.assertEqual(stats["language"], "en")
        self.assertEqual(stats["by_length"][5]["allowed_guesses"], 8714)
        self.assertEqual(stats["by_length"][5]["answer_words"], 3436)
        self.assertEqual(stats["by_length"][5]["blocked_words"], 51)
        self.assertEqual(stats["blocked_words"], 291)
        self.assertEqual(get_dictionary_stats("en", 5)["answer_words"], 3436)
        self.assertEqual(get_dictionary_stats("en", 6)["allowed_guesses"], 0)

    def test_aliases_share_one_cached_dictionary_and_unknown_languages_fail_clearly(self):
        self.assertIs(load_dictionary("en"), load_dictionary("en-US"))
        with self.assertRaisesRegex(DictionaryLoadError, "Unsupported dictionary language"):
            load_dictionary("fr")

    def test_loader_reports_missing_or_malformed_dictionary_files(self):
        root = Path(__file__).parent
        allowed = root / ".word-dictionary-v2-allowed-test.txt"
        answers = root / ".word-dictionary-v2-answers-test.txt"
        allowed_four = root / ".word-dictionary-v2-allowed-four-test.txt"
        answers_four = root / ".word-dictionary-v2-answers-four-test.txt"
        allowed_six = root / ".word-dictionary-v2-allowed-six-test.txt"
        answers_six = root / ".word-dictionary-v2-answers-six-test.txt"
        blocked = root / ".word-dictionary-v2-blocked-test.txt"
        try:
            allowed.write_text("CRANE\ncrane\n", encoding="utf-8")
            answers.write_text("CRANE\n", encoding="utf-8")
            blocked.write_text("# no entries\n", encoding="utf-8")
            loaded = WordDictionary.from_files({5: allowed}, {5: answers}, blocked)
            self.assertTrue(loaded.is_valid_guess("crane"))

            allowed_four.write_text("ABLE\n", encoding="utf-8")
            answers_four.write_text("ABLE\n", encoding="utf-8")
            allowed_six.write_text("PLANET\n", encoding="utf-8")
            answers_six.write_text("PLANET\n", encoding="utf-8")
            flexible = WordDictionary.from_files(
                {4: allowed_four, 6: allowed_six},
                {4: answers_four, 6: answers_six},
                blocked,
            )
            self.assertTrue(flexible.is_valid_guess("able", 4))
            self.assertTrue(flexible.is_allowed_answer("planet", 6))
            self.assertEqual(flexible.stats()["by_length"][6]["answer_words"], 1)

            with self.assertRaisesRegex(DictionaryLoadError, "Could not load"):
                WordDictionary.from_files(
                    {5: root / ".word-dictionary-v2-missing.txt"}, {5: answers}, blocked
                )

            answers.write_text("wrong-length\n", encoding="utf-8")
            with self.assertRaisesRegex(DictionaryLoadError, "No answer words"):
                WordDictionary.from_files({5: allowed}, {5: answers}, blocked)
        finally:
            for path in (allowed, answers, allowed_four, answers_four, allowed_six, answers_six, blocked):
                path.unlink(missing_ok=True)

    def test_existing_guess_response_contract_remains_stable(self):
        self.assertEqual(validate_guess("CRANE", word_length=5), ("CRANE", None))
        self.assertEqual(validate_guess("qzxjk", word_length=5), (None, "Not in word list"))
        self.assertEqual(
            validate_guess("four", word_length=5),
            (None, "Word must be 5 letters long"),
        )


if __name__ == "__main__":
    unittest.main()
