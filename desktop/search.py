# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The search box language shared by the player and the editor: words, "=whole
# words", "quoted phrases" and "field:word" tests.
from __future__ import annotations

import re

SEARCH_FIELDS = {"title", "artist", "album", "genre", "composer", "path"}
SEARCH_TOKEN = re.compile(r'(?:(\w+):)?(?:"([^"]*)"?|(\S+))')


def parse_search(
    text: str, fields=SEARCH_FIELDS
) -> list[tuple[str | None, re.Pattern]]:
    # Turn the search box text into (field, pattern) tests that must all match.
    #
    # A word matches where a word of the text starts with it ("dio" finds Dio
    # and Dion, not audio or studio);
    # a leading "=" asks for the whole word ("=dio" finds only Dio); "quoted
    # words" match as a phrase;
    # "artist:dio" limits the test to one field (title, artist, album, genre,
    # composer, path).
    tests = []
    for field, quoted, word in SEARCH_TOKEN.findall(text.lower()):
        word = quoted if quoted else word
        if (
            field and field not in fields
        ):  # not a field name: search for the text as typed
            word, field = f"{field}:{word}", ""
        whole = word.startswith("=")
        word = word.lstrip("=").strip()
        if not word:
            continue
        pattern = (
            r"(?<![^\W_])" + re.escape(word) + (r"(?![^\W_])" if whole else "")
        )  # boundaries: not inside a word
        tests.append((field or None, re.compile(pattern)))
    return tests


def search_fields(path: str, tags: dict[str, list[str]]) -> dict[str, str]:
    # The lower case text of each searchable field of one file.
    return {
        "title": "\n".join(tags.get("TITLE", [])).lower(),
        "artist": (
            "\n".join(
                tags.get("ARTIST", []) + tags.get("ALBUMARTIST", [])
            ).lower()
        ),
        "album": "\n".join(tags.get("ALBUM", [])).lower(),
        "genre": "\n".join(tags.get("GENRE", [])).lower(),
        "composer": "\n".join(tags.get("COMPOSER", [])).lower(),
        "path": path.lower(),
    }
