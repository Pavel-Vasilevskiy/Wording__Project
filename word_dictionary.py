"""Local, reusable word dictionaries for the Wordle game."""

from __future__ import annotations

import random
import re
from functools import lru_cache
from pathlib import Path

_VALID_GUESS = re.compile(r"[A-Za-z]{5}\Z")
_DICTIONARY_ROOT = Path(__file__).resolve().parent / "dictionaries"


class DictionaryLoadError(RuntimeError):
    """Raised when a required dictionary cannot be loaded safely."""


def normalize_guess(value: object) -> str | None:
    """Return a lowercase ASCII five-letter guess, or None for invalid input."""
    if not isinstance(value, str):
        return None

    if _VALID_GUESS.fullmatch(value) is None:
        return None
    return value.lower()


class WordDictionary:
    """A five-letter dictionary with fast membership checks and random choice."""

    def __init__(self, words: set[str]) -> None:
        if not words:
            raise DictionaryLoadError("Dictionary contains no valid five-letter words")

        self.words = tuple(sorted(words))
        self._word_set = frozenset(self.words)

    @classmethod
    def from_file(cls, path: str | Path) -> WordDictionary:
        """Load, normalize, and deduplicate valid words from a UTF-8 text file."""
        source = Path(path)
        words: set[str] = set()
        try:
            with source.open("r", encoding="utf-8") as dictionary_file:
                for line in dictionary_file:
                    candidate = line.rstrip("\r\n")
                    if _VALID_GUESS.fullmatch(candidate) is not None:
                        words.add(candidate.lower())
        except (OSError, UnicodeError) as error:
            raise DictionaryLoadError(
                f"Could not load word dictionary from {source}: {error}"
            ) from error

        if not words:
            raise DictionaryLoadError(
                f"Word dictionary {source} contains no valid five-letter words"
            )
        return cls(words)

    def __len__(self) -> int:
        return len(self.words)

    def contains(self, value: object) -> bool:
        normalized = normalize_guess(value)
        return normalized is not None and normalized in self._word_set

    def choose_answer(self) -> str:
        """Choose uniformly from the complete loaded dictionary."""
        return random.choice(self.words)


def validate_guess(value: object, dictionary: WordDictionary) -> tuple[str | None, str | None]:
    """Return the normalized uppercase guess or the existing API error text."""
    if not isinstance(value, str) or len(value) != 5:
        return None, "Word must be 5 letters long"

    normalized = normalize_guess(value)
    if normalized is None or not dictionary.contains(normalized):
        return None, "Not in word list"
    return normalized.upper(), None


@lru_cache(maxsize=None)
def load_dictionary(language: str = "en-US") -> WordDictionary:
    """Load a locale dictionary once per process, ready for future languages."""
    path = _DICTIONARY_ROOT / language / "words.txt"
    return WordDictionary.from_file(path)


# Load eagerly during application import so a missing or broken production
# dictionary fails clearly rather than silently accepting arbitrary guesses.
EN_US_DICTIONARY = load_dictionary("en-US")
