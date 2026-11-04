# en-US Word Dictionary

This directory contains local, generated data for both game modes. The app
loads each runtime set once at startup; guesses and answers are kept separate.

## Runtime files

- `allowed_guesses_5.txt`: five-letter entries accepted as guesses.
- `answer_words_5.txt`: five-letter answer candidates, always a subset of the
  allowed guesses.
- `blocked_words.txt`: exact ASCII terms excluded from configured sets. It also
  retains other lengths so future modes can reuse the same maintained list.

Allowed guesses and answers are lowercase ASCII `a-z`, unique, sorted, and
exactly five characters long. Blocked entries are lowercase ASCII words of
different lengths as well. `build_dictionary.py` recreates the runtime files
from pinned source snapshots and reviewed additions in
`source/blocklist-extra.txt`.

## Allowed guesses and answer words

The broad guess list is the project's existing English Speller Database
(ESDB/SCOWL) American large export, release `rel-2026.02.25`, generated as
`en_US-large.txt`. Its source and required notice are in `source/` and
`source/Copyright`. The project mechanically retains only five ASCII letters,
lowercases, deduplicates, and removes blocked exact words. No rarity cutoff is
applied to guesses.

- Source snapshot SHA-256: `fa1f9a1382df724be887e3a5d2d743095e6f33fc0949ebb22415c1554bb42fa7`
- Runtime count after filtering and blacklist: **8,714 allowed guesses**.

The answer list is generated from the official ESDB custom word-list creator
for the `2026.02.25` dataset, with `Size=35 (small)`, `Spelling=US`,
`Variant Level=1`, diacritics stripped, and no special hacker or Roman numeral
lists. The captured generator revisions are ESDB `1e5b7d3` and app `156fb4e`;
the full generator metadata is preserved in `source/en_US-size35.txt`.
Retrieval date: 2026-11-03. Size 35 is
the source's common/small tier, not a proprietary game answer list. The same
mechanical five-letter ASCII filter, blacklist, and subset check are applied.
The ESDB/SCOWL permission notice permits redistribution of generated lists;
retain `source/Copyright` with the packaged source.

- Source snapshot SHA-256: `3b0c2a2d20c4711f53a65fdb38d663df05092a24120a14a71b18deae64082bd5`
- Runtime count after filtering, blacklist, and allowed-set intersection:
  **3,436 answer words**.

Source URLs:

- https://github.com/en-wl/wordlist/tree/rel-2026.02.25
- https://app.aspell.net/create?max_size=35&spelling=US&variant_level=1&diacritic=strip&download=wordlist&encoding=utf-8&format=inline

## Blocklist

`source/LDNOOBW-en.txt` is the English list from LDNOOBW, pinned to commit
`5faf2ba42d7b1c0977169ec3611df25a3c08eb13` and retrieved 2026-11-03. It is
licensed under Creative Commons Attribution 4.0; its license is included as
`source/LDNOOBW-LICENSE.txt`. The build keeps only exact five-letter ASCII
entries from it and combines them with `source/blocklist-extra.txt`, which is
a project-maintained set of reviewed variants. The filter compares whole
normalized words only. It never rejects a word because it contains a blocked
letter sequence; ordinary words such as `class` and `assay` remain eligible.

- LDNOOBW source SHA-256: `af851ecef1d5f212caba17339b12ac39cc2fef7d78c74876f67237644fcee8bd`
- Runtime blacklist count: **291 exact ASCII terms across lengths**, including
  **51 five-letter terms** used by the current game.

## Build and maintenance

Run from the project root:

```bash
python build_dictionary.py
```

Review source/license updates and the blocked-term list before replacing the
pinned snapshots. Then rebuild and run `python -m unittest discover -s tests -q`.
The runtime does not download dictionaries or call a remote API.

## Counts and integrity

The counts above are recorded in the build output and covered by tests. Sources
are normalized and deduplicated during every build; therefore
the generated lists contain no duplicates. The runtime loader also verifies
that answer words belong to the allowed-guess set and fails clearly if any
required file is missing or malformed.

The dictionary module accepts `en` and `en-US` for English. Sibling locale
directories and length-specific files (for example `allowed_guesses_6.txt` and
`answer_words_6.txt`) can be added later without changing the game-language
API. Only English five-letter play is configured in this iteration.
