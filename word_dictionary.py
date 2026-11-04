"""Central, local word dictionaries shared by all game modes.

Dictionary files are packaged below ``dictionaries/<locale>``. Guesses and
answers are loaded separately and cached once per locale. The legacy
``EN_US_DICTIONARY`` and ``validate_guess`` APIs remain available to keep
existing callers and stored game sessions compatible.
"""

from __future__ import annotations

import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Iterable


_WORD = re.compile(r"[A-Za-z]+\Z", re.ASCII)
_DICTIONARY_ROOT = Path(__file__).resolve().parent / "dictionaries"
_LANGUAGE_DIRECTORIES = {
    "en": "en-US",
    "en-us": "en-US",
    "en_us": "en-US",
    "uk": "uk",
    "pl": "pl",
}


class DictionaryLoadError(RuntimeError):
    """Raised when a required dictionary cannot be loaded safely."""


def _canonical_language(language: str) -> tuple[str, str]:
    if not isinstance(language, str):
        raise DictionaryLoadError("Dictionary language must be a string")
    key = language.strip().lower()
    directory = _LANGUAGE_DIRECTORIES.get(key)
    if directory is None:
        raise DictionaryLoadError(f"Unsupported dictionary language: {language!r}")
    return ("en" if directory == "en-US" else directory, directory)


def normalize_word(value: object, word_length: int | None = None) -> str | None:
    """Normalize ASCII letters to lowercase; reject malformed or wrong-length input."""
    if not isinstance(value, str) or _WORD.fullmatch(value) is None:
        return None
    if word_length is not None and (not isinstance(word_length, int) or len(value) != word_length):
        return None
    return value.lower()


def normalize_guess(value: object, word_length: int = 5) -> str | None:
    """Backward-compatible name for normalizing a guess."""
    return normalize_word(value, word_length)


def _read_word_file(path: Path, *, required: bool = True) -> set[str]:
    words: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                candidate = line.strip()
                if not candidate or candidate.startswith("#"):
                    continue
                if _WORD.fullmatch(candidate):
                    words.add(candidate.lower())
    except (OSError, UnicodeError) as error:
        if not required and isinstance(error, FileNotFoundError):
            return words
        raise DictionaryLoadError(f"Could not load word dictionary from {path}: {error}") from error
    return words


class WordDictionary:
    """Cached allowed-guess and answer vocabularies, grouped by word length."""

    def __init__(
        self,
        allowed_by_length: dict[int, set[str]],
        answers_by_length: dict[int, set[str]],
        blocked_words: set[str] | None = None,
        language: str = "en",
    ) -> None:
        self.language = language
        self.blocked_words = frozenset(blocked_words or ())
        self._allowed = {length: frozenset(words) for length, words in allowed_by_length.items()}
        self._answers = {length: frozenset(words) for length, words in answers_by_length.items()}
        self._allowed_sorted = {
            length: tuple(sorted(words)) for length, words in self._allowed.items()
        }
        self._answers_sorted = {
            length: tuple(sorted(words)) for length, words in self._answers.items()
        }
        if not self._allowed or not self._answers:
            raise DictionaryLoadError("Dictionary must contain allowed guesses and answer words")
        for length, answers in self._answers.items():
            allowed = self._allowed.get(length)
            if not allowed:
                raise DictionaryLoadError(f"No allowed guesses are configured for {length} letters")
            if not answers:
                raise DictionaryLoadError(f"No answer words are configured for {length} letters")
            if not answers.issubset(allowed):
                raise DictionaryLoadError(f"Answer words of length {length} are not all allowed guesses")
            if answers.intersection(self.blocked_words):
                raise DictionaryLoadError(f"Blocked words are present in the {length}-letter answer set")
        if any(not words or any(len(word) != length for word in words) for length, words in self._allowed.items()):
            raise DictionaryLoadError("Allowed-guess files contain no valid words for a configured length")

    @classmethod
    def from_files(
        cls,
        allowed_files: dict[int, str | Path],
        answer_files: dict[int, str | Path],
        blocked_file: str | Path,
        *,
        language: str = "en",
    ) -> WordDictionary:
        blocked = _read_word_file(Path(blocked_file))
        allowed = {
            length: _read_word_file(Path(path)) - blocked
            for length, path in allowed_files.items()
        }
        answers = {
            length: _read_word_file(Path(path)) - blocked
            for length, path in answer_files.items()
        }
        return cls(allowed, answers, blocked, language)

    @classmethod
    def from_file(cls, path: str | Path, word_length: int = 5) -> WordDictionary:
        """Compatibility helper for tests and small standalone dictionaries.

        A single file is explicitly used for both roles here. Production
        dictionaries must use ``from_files`` to keep the roles separate.
        """
        words = _read_word_file(Path(path))
        words = {word for word in words if len(word) == word_length}
        if not words:
            length_label = "five-letter" if word_length == 5 else f"{word_length}-letter"
            raise DictionaryLoadError(
                f"Word dictionary {path} contains no valid {length_label} words"
            )
        return cls({word_length: words}, {word_length: words})

    @property
    def words(self) -> tuple[str, ...]:
        """Legacy five-letter allowed-guess list."""
        return self.allowed_words(5)

    def allowed_words(self, word_length: int = 5) -> tuple[str, ...]:
        return self._allowed_sorted.get(word_length, ())

    def answer_words(self, word_length: int = 5) -> tuple[str, ...]:
        return self._answers_sorted.get(word_length, ())

    def is_valid_guess(self, value: object, word_length: int = 5) -> bool:
        normalized = normalize_word(value, word_length)
        return normalized is not None and normalized in self._allowed.get(word_length, ())

    def is_allowed_answer(self, value: object, word_length: int = 5) -> bool:
        normalized = normalize_word(value, word_length)
        return normalized is not None and normalized in self._answers.get(word_length, ())

    def contains(self, value: object, word_length: int = 5) -> bool:
        """Legacy membership API means an allowed guess."""
        return self.is_valid_guess(value, word_length)

    def choose_answer(
        self,
        word_length: int = 5,
        *,
        random_source=None,
        exclude: Iterable[str] = (),
    ) -> str:
        candidates = self.answer_words(word_length)
        excluded = {word.lower() for word in exclude if isinstance(word, str)}
        choices = [word for word in candidates if word not in excluded]
        if not choices:
            choices = list(candidates)
        if not choices:
            raise DictionaryLoadError(f"No answer words are available for {word_length} letters")
        source = random_source or secrets.SystemRandom()
        return source.choice(choices)

    def choose_answers(self, count: int, word_length: int = 5, *, random_source=None) -> list[str]:
        candidates = self.answer_words(word_length)
        if count < 1 or count > len(candidates):
            raise ValueError("Dictionary cannot supply the requested number of unique answers")
        source = random_source or secrets.SystemRandom()
        return source.sample(candidates, count)

    def __len__(self) -> int:
        return len(self._allowed.get(5, ()))

    def stats(self) -> dict:
        blocked_by_length: dict[int, int] = {}
        for word in self.blocked_words:
            blocked_by_length[len(word)] = blocked_by_length.get(len(word), 0) + 1
        lengths = sorted(set(self._allowed) | set(self._answers) | set(blocked_by_length))
        return {
            "language": self.language,
            "allowed_guesses": sum(len(self._allowed.get(length, ())) for length in lengths),
            "answer_words": sum(len(self._answers.get(length, ())) for length in lengths),
            "blocked_words": len(self.blocked_words),
            "by_length": {
                length: {
                    "allowed_guesses": len(self._allowed.get(length, ())),
                    "answer_words": len(self._answers.get(length, ())),
                    "blocked_words": blocked_by_length.get(length, 0),
                }
                for length in lengths
            },
        }


@lru_cache(maxsize=None)
def _load_dictionary_directory(directory: str, public_language: str) -> WordDictionary:
    root = _DICTIONARY_ROOT / directory
    blocked_file = root / "blocked_words.txt"
    allowed_files = {}
    answer_files = {}
    for path in sorted(root.glob("allowed_guesses_*.txt")):
        match = re.fullmatch(r"allowed_guesses_([0-9]+)\.txt", path.name)
        if match:
            allowed_files[int(match.group(1))] = path
    for path in sorted(root.glob("answer_words_*.txt")):
        match = re.fullmatch(r"answer_words_([0-9]+)\.txt", path.name)
        if match:
            answer_files[int(match.group(1))] = path
    if not blocked_file.is_file() or not allowed_files or not answer_files:
        raise DictionaryLoadError(
            f"Dictionary files are incomplete for language {public_language!r} in {root}"
        )
    return WordDictionary.from_files(
        allowed_files, answer_files, blocked_file, language=public_language
    )


def load_dictionary(language: str = "en") -> WordDictionary:
    """Load a locale once, canonicalizing aliases to avoid duplicate copies."""
    public_language, directory = _canonical_language(language)
    return _load_dictionary_directory(directory, public_language)


def is_valid_guess(word: object, language: str = "en", word_length: int = 5) -> bool:
    return load_dictionary(language).is_valid_guess(word, word_length)


def is_allowed_answer(word: object, language: str = "en", word_length: int = 5) -> bool:
    return load_dictionary(language).is_allowed_answer(word, word_length)


def get_random_answer(
    language: str = "en",
    word_length: int = 5,
    *,
    random_source=None,
    exclude: Iterable[str] = (),
) -> str:
    return load_dictionary(language).choose_answer(
        word_length, random_source=random_source, exclude=exclude
    )


def get_random_answers(
    count: int,
    language: str = "en",
    word_length: int = 5,
    *,
    random_source=None,
) -> list[str]:
    return load_dictionary(language).choose_answers(
        count, word_length, random_source=random_source
    )


def get_dictionary_stats(language: str = "en", word_length: int | None = None) -> dict:
    stats = load_dictionary(language).stats()
    if word_length is None:
        return stats
    per_length = stats["by_length"].get(word_length, {
        "allowed_guesses": 0,
        "answer_words": 0,
    })
    return {
        "language": stats["language"],
        **per_length,
        "blocked_words": per_length.get("blocked_words", 0),
    }


def validate_guess(
    value: object,
    dictionary: WordDictionary | None = None,
    word_length: int = 5,
    *,
    language: str = "en",
) -> tuple[str | None, str | None]:
    """Return a normalized uppercase guess and the established API error text."""
    if not isinstance(value, str) or len(value) != word_length:
        return None, f"Word must be {word_length} letters long"
    normalized = normalize_word(value, word_length)
    source = dictionary or load_dictionary(language)
    if normalized is None or not source.is_valid_guess(normalized, word_length):
        return None, "Not in word list"
    return normalized.upper(), None


# Fail during application initialization if the required en-US data is missing.
EN_US_DICTIONARY = load_dictionary("en-US")
