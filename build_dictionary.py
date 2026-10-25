"""Build the normalized runtime dictionary from its pinned SCOWL source."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "dictionaries" / "en-US" / "source" / "en_US-large.txt"
OUTPUT = ROOT / "dictionaries" / "en-US" / "words.txt"
WORD = re.compile(r"[A-Za-z]{5}\Z")


def build_dictionary() -> int:
    words: set[str] = set()
    with SOURCE.open("r", encoding="utf-8") as source_file:
        for line in source_file:
            candidate = line.rstrip("\r\n")
            if WORD.fullmatch(candidate) is not None:
                words.add(candidate.lower())

    if not words:
        raise ValueError(f"No five-letter ASCII words found in {SOURCE}")

    OUTPUT.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8", newline="\n")
    return len(words)


if __name__ == "__main__":
    print(f"Wrote {build_dictionary():,} words to {OUTPUT}")
