# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Rules for dynamic playlists: validates them and selects matching tracks.
#
# A dynamic playlist is a saved rule set; rules can be grouped, and groups
# nested, each group with its own
# all / any:  (genre is soundtrack and (title contains star or title contains
# raiders)) or (genre is rock and ...).
# A dynamic playlist is a saved rule set.  The tracks it holds are selected from
# the library by the rules
# (duplicates are never selected), at most as many as the configured length; the
# first ones in the chosen
# sort order, or random ones.  rules is a dict:
#
#     {"match": "all" | "any",
#      "rules": [{"field": "artist", "op": "contains", "value": "dio"},
#                {"match": "any", "rules": [...]},          # a group: the same
#                shape, without "sort"
#                ...],
#      "sort":  {"field": "random" | "artist" | ..., "descending": False},
#      "prefer": ["flac", "m4a", "mp3"]}          # optional: file types in
#      order of preference
#
# "prefer" fills the list from the first type, going on to the next only when
# there are not enough tracks of the types
# before it (types not listed come last); within a type the sort order applies.
#
# A text rule has a text value, a number rule a whole number, a date rule a
# number of days; some operators
# take no value.  A track with no value for a field (not rated, never played, no
# BPM) fails every comparison
# except "is not set" / "never" / "is empty" and the negative text operators.
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone

TEXT_FIELDS = {
    "artist": "Artist",
    "title": "Title",
    "album": "Album",
    "genre": "Genre",
    "composer": "Composer",
    "path": "Path",
    "format": "Format (file type, e.g. flac)",
}
NUMBER_FIELDS = {
    "rating": "Rating",
    "plays": "Play count",
    "year": "Year",
    "bpm": "BPM",
    "bpm_reliability": "BPM reliability (0 low, 1 medium, 2 high)",
    "track": "Track number",
}
DATE_FIELDS = {"last_played": "Last played"}
FIELDS = {**TEXT_FIELDS, **NUMBER_FIELDS, **DATE_FIELDS}
OPS = {
    "text": [
        ("contains", "contains"),
        ("not_contains", "does not contain"),
        ("is", "is"),
        ("is_not", "is not"),
        ("empty", "is empty"),
    ],
    "number": [
        ("eq", "="),
        ("ne", "≠"),
        ("lt", "<"),
        ("le", "≤"),
        ("gt", ">"),
        ("ge", "≥"),
        ("unset", "is not set"),
        ("set", "is set"),
    ],
    "date": [
        ("within", "within the last (days)"),
        ("not_within", "more than (days) ago, or never"),
        ("never", "never"),
    ],
}
NO_VALUE = {"empty", "unset", "set", "never"}
SORTS = {
    "random": "Random",
    "artist": "Artist",
    "album": "Album",
    "title": "Title",
    "year": "Year",
    "rating": "Rating",
    "plays": "Play count",
    "last_played": "Last played",
    "bpm": "BPM",
}
DEFAULT_LENGTH = 20
EMPTY_RULES = {
    "match": "all",
    "rules": [],
    "sort": {"field": "random", "descending": False},
}


def field_kind(field: str) -> str:
    return (
        "text"
        if field in TEXT_FIELDS
        else "number" if field in NUMBER_FIELDS else "date"
    )


def word_regex(word: str, whole: bool = False) -> re.Pattern:
    # Where a word of the text starts with this word (or, if whole, is exactly
    # it): not inside a word.
    return re.compile(
        r"(?<![^\W_])"
        + re.escape(word.lower())
        + (r"(?![^\W_])" if whole else "")
    )


def describe(rules: dict) -> str:
    # The rule set in words, one rule per line, groups indented.
    labels = {kind: dict(ops) for kind, ops in OPS.items()}
    lines = []

    def rule_text(rule: dict) -> str:
        field, op = rule["field"], rule["op"]
        if field_kind(field) == "date" and op != "never":
            days = rule["value"]
            return (
                f"Last played within the last {days} days"
                if op == "within"
                else f"Last played more than {days} days ago, or never"
            )
        text = f"{FIELDS[field].split(' (')[0]} {labels[field_kind(field)][op]}"
        return text if op in NO_VALUE else f"{text} {rule['value']}"

    def group(g: dict, depth: int) -> None:
        pad = "    " * depth
        lines.append(
            f"{pad}Match {'all' if g['match'] == 'all' else 'any'} of:"
            if depth
            else (
                f"Match {'all' if g['match'] == 'all' else 'any'} of these"
                " rules:"
            )
        )
        for item in g["rules"]:
            if "field" in item:
                lines.append(f"{pad}    \u2022 {rule_text(item)}")
            else:
                group(item, depth + 1)
        if not g["rules"]:
            lines.append(f"{pad}    (no rules: every track)")

    group(rules, 0)
    sort = rules.get("sort", {})
    order = SORTS.get(sort.get("field"), "Random")
    lines.append(
        f"Choose in the order: {order}"
        + (", descending" if sort.get("descending") else "")
    )
    if rules.get("prefer"):
        lines.append("Prefer file types: " + ", ".join(rules["prefer"]))
    return "\n".join(lines)


def validate(rules: dict) -> str | None:
    # None if the rule set is usable, else what is wrong with it.
    if rules.get("sort", {}).get("field") not in SORTS:
        return "Unknown sort order."
    prefer = rules.get("prefer", [])
    if not isinstance(prefer, list) or not all(
        isinstance(x, str) and re.fullmatch(r"[a-z0-9]+", x) for x in prefer
    ):
        return (
            "Preferred formats must be file types such as flac, mp3 (lower"
            " case, separated by commas)."
        )
    return _validate_group(rules, "", top=True)


def _validate_group(group: dict, where: str, top: bool = False) -> str | None:
    if group.get("match") not in ("all", "any"):
        return f"{where or 'The rules'}: match must be 'all' or 'any'."
    items = group.get("rules")
    if not isinstance(items, list):
        return f"{where or 'The rules'}: no rule list."
    if not items and not top:
        return f"{where.rstrip(': ')} is empty: add a rule or remove it."
    for n, item in enumerate(items, 1):
        label = f"{where}rule {n}" if "field" in item else f"{where}group {n}"
        if "field" not in item:
            error = _validate_group(item, f"{label}: ")
        else:
            error = _validate_rule(item, f"{label}: ")
        if error:
            return error
    return None


def _validate_rule(rule: dict, where: str) -> str | None:
    field, op = rule.get("field"), rule.get("op")
    if field not in FIELDS:
        return f"{where}unknown field."
    if op not in dict(OPS[field_kind(field)]):
        return f"{where}{FIELDS[field]} cannot use '{op}'."
    if op in NO_VALUE:
        return None
    value = rule.get("value")
    if field_kind(field) == "text":
        if not isinstance(value, str) or not value.strip():
            return f"{where}give {FIELDS[field]} a value."
    elif not isinstance(value, int) or isinstance(value, bool):
        return f"{where}{FIELDS[field]} needs a whole number."
    return None


def _first_int(text: str) -> int | None:
    m = re.match(r"\s*(\d+)", text)
    return int(m.group(1)) if m else None


def file_format(path: str) -> str:
    # The file type: the extension in lower case, without the dot ("flac").
    return (
        path.rsplit(".", 1)[-1].lower()
        if "." in path.rsplit("/", 1)[-1]
        else ""
    )


def parse_prefer(text: str) -> list[str]:
    # "FLAC, .m4a mp3" -> ["flac", "m4a", "mp3"].
    return list(
        dict.fromkeys(
            w.lower().lstrip(".")
            for w in re.split(r"[,\s]+", text)
            if w.strip(".")
        )
    )


def values(db, path: str, field: str):
    # A file's value(s) for a field: a list of strings (text), an int or None
    # (number), an ISO string or None (date).
    tags = db.tags[path]
    if field == "path":
        return [path]
    if field == "format":
        return [file_format(path)]
    if field in TEXT_FIELDS:
        keys = (
            ("ARTIST", "ALBUMARTIST") if field == "artist" else (field.upper(),)
        )
        return [v for k in keys for v in tags.get(k, []) if v.strip()]
    rating, plays, last = db.stats.get(path, (None, 0, None))
    if field == "rating":
        return rating
    if field == "plays":
        return plays
    if field == "last_played":
        return last
    key = {
        "year": "DATE",
        "bpm": "BPM",
        "bpm_reliability": "BPM_RELIABILITY",
        "track": "TRACKNUMBER",
    }[field]
    return next(
        (n for v in tags.get(key, []) if (n := _first_int(v)) is not None), None
    )


def _test(rule: dict, now: datetime):
    # A function path-values -> bool for one rule.
    field, op, value = rule["field"], rule["op"], rule.get("value")
    kind = field_kind(field)
    if kind == "text":
        word = value.strip().lower() if isinstance(value, str) else ""
        contains = word_regex(word)
        whole = word.casefold()
        return {
            "contains": lambda vs: any(contains.search(v.lower()) for v in vs),
            "not_contains": lambda vs: not any(
                contains.search(v.lower()) for v in vs
            ),
            "is": lambda vs: any(v.strip().casefold() == whole for v in vs),
            "is_not": lambda vs: not any(
                v.strip().casefold() == whole for v in vs
            ),
            "empty": lambda vs: not vs,
        }[op]
    if kind == "number":
        return {
            "eq": lambda n: n is not None and n == value,
            "ne": lambda n: n is not None and n != value,
            "lt": lambda n: n is not None and n < value,
            "le": lambda n: n is not None and n <= value,
            "gt": lambda n: n is not None and n > value,
            "ge": lambda n: n is not None and n >= value,
            "unset": lambda n: n is None,
            "set": lambda n: n is not None,
        }[op]
    since = (
        (now - timedelta(days=value)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, int)
        else ""
    )
    return {
        "within": lambda last: bool(last) and last >= since,
        "not_within": lambda last: not last or last < since,
        "never": lambda last: not last,
    }[op]


def _compile(db, group: dict, now: datetime):
    # A function path -> bool for a group of rules (and groups).
    parts = []
    for item in group["rules"]:
        if "field" in item:
            test, field = _test(item, now), item["field"]
            parts.append(
                lambda p, test=test, field=field: test(values(db, p, field))
            )
        else:
            parts.append(_compile(db, item, now))
    combine = all if group["match"] == "all" else any
    return lambda p: combine(part(p) for part in parts)


def matches(db, rules: dict, exclude=frozenset()) -> list[str]:
    # Every library file the rules select (never a duplicate, never one of
    # exclude), sorted as the rules say
    # (random for "random").
    error = validate(rules)
    if error:
        raise ValueError(error)
    selects = (
        _compile(db, rules, datetime.now(timezone.utc))
        if rules["rules"]
        else (lambda _p: True)
    )
    chosen = [
        p
        for p in db.tags
        if p not in db.dup_of and p not in exclude and selects(p)
    ]
    sort = rules["sort"]
    if sort["field"] == "random":
        random.shuffle(chosen)
    else:
        chosen.sort(
            key=lambda p: _sort_key(db, p, sort["field"]),
            reverse=bool(sort.get("descending")),
        )
    prefer = rules.get("prefer") or []
    if prefer:  # stable: the order inside each file type stays as sorted
        rank = {fmt: n for n, fmt in enumerate(prefer)}
        chosen.sort(key=lambda p: rank.get(file_format(p), len(prefer)))
    return chosen


def _sort_key(db, path: str, field: str):
    # The sort field first (not set sorts lowest), then artist / album / track
    # so that ties come out in album order.
    def text(f: str) -> str:
        return " ".join(values(db, path, f)).casefold()

    if field in ("artist", "album", "title"):
        primary = text(field)
    elif field == "last_played":
        primary = values(db, path, field) or ""
    else:
        v = values(db, path, field)
        primary = -1 if v is None else v
    return (
        primary,
        text("artist"),
        text("album"),
        values(db, path, "track") or 0,
        text("title"),
    )
