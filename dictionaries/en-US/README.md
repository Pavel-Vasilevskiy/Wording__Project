# en-US game dictionary

- **Source:** [English Speller Database (ESDB/SCOWL) generated `en_US-large.txt`](https://github.com/en-wl/wordlist-diff/blob/rel-2026.02.25/en_US-large.txt)
- **Release:** `rel-2026.02.25` / ESDB 2026.02.25, American English, large (SCOWL size 70)
- **Retrieved:** 2026-10-25
- **License:** SCOWL permissive MIT-like notice; see [`source/Copyright`](source/Copyright). The notice grants use, modification, and redistribution of word lists created from SCOWL, subject to retaining the copyright and permission notice.
- **Source snapshot SHA-256:** `fa1f9a1382df724be887d3a5d2d743095e6f33fc0949ebb22415c1554bb42fa7`
- **Filtered words:** 8,754 unique entries.

`source/en_US-large.txt` is the retrieved upstream snapshot. Generate the runtime
list with `python build_dictionary.py`. The build keeps only lines consisting of
exactly five ASCII letters `A-Z`/`a-z`, lowercases them, removes duplicates, and
writes the result sorted to `words.txt`. No common-word cutoff or additional
content filter is applied. This means rare words, inflections, variant forms,
and any name-like entries already present in the upstream spell-checker list
remain eligible if they pass the stated character and length filter. The source
maintainer notes that the large list is less carefully checked than the default
list and can contain questionable entries; this project intentionally applies
only the requested mechanical filter.

The runtime uses `words.txt`; it does not read from or store the dictionary in
the SQLite database or Fly Volume. New locales can be packaged in sibling
directories such as `dictionaries/fr-FR/`.
