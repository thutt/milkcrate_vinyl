#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Estimates and stores the tempo (BPM) of files whose BPM is unknown.
#
#   bpm_batch.py --database DB analyse [--limit N] [--jobs N]
#         read-only for the tags: estimate the tempo of the files whose BPM is 0
#         and keep the results in the database's
#         bpm_analysis table (one row per file: BPM, reliability, windows, ...)
#   bpm_batch.py --database DB apply
#         back up the database, journal, then store the results marked high or
#         medium reliability as the BPM tag (low
#         reliability files stay 0 and are listed in bpm_analysis for
#         follow-up); verifies by re-reading
#   bpm_batch.py --database DB reliability
#         store BPM for the low-reliability files too, and BPM_RELIABILITY (0
#         low, 1 medium, 2 high) for every analysed
#         file (backup, journal, verification as for apply)
#
# The media files are never touched; only the BPM tags in the database change.
# The journal is written next to the
# database as bpm-detect-journal.jsonl.
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

from file_analysis import bpm_one as one
from schema import BPM_ANALYSIS_SCHEMA
from tagdb import TagDb, normalize

LEVEL = {"low": 0, "medium": 1, "high": 2}


def first(db: TagDb, path: str, name: str) -> str:
    values = db.tags[path].get(name) or [""]
    return values[0]


def analyse(db: TagDb, limit: int | None, jobs: int) -> None:
    root = db.root()
    paths = [p for p in db.tags if first(db, p, "BPM") == "0"]
    if limit:
        paths = paths[:limit]
    print(f"{len(paths)} files to analyse under {root}", file=sys.stderr)
    results, start = {}, time.time()
    with ProcessPoolExecutor(max_workers=max(1, jobs)) as pool:
        for n, (rel, res, error) in enumerate(
            pool.map(one, [(root, p) for p in paths], chunksize=8), 1
        ):
            results[rel] = (res, error)
            if n % 500 == 0 or n == len(paths):
                print(
                    f"  {n}/{len(paths)}  {time.time() - start:.0f}s",
                    file=sys.stderr,
                )
    groups = defaultdict(list)  # the same song's copies should agree
    for rel, (res, _e) in results.items():
        title, artist = first(db, rel, "TITLE"), first(db, rel, "ARTIST")
        if res["reliability"] != "low" and title and artist:
            groups[(normalize(artist), normalize(title))].append(
                (rel, res["bpm"])
            )
    disagree = {
        rel
        for g in groups.values()
        if len(g) > 1 and max(b for _r, b in g) - min(b for _r, b in g) > 10
        for rel, _b in g
    }
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.con:
        db.con.executescript(BPM_ANALYSIS_SCHEMA)
        db.con.execute("DELETE FROM bpm_analysis")
        db.con.executemany(
            "INSERT INTO bpm_analysis(file_id, bpm, reliability, agree,"
            " windows, prominence, tempos, copies_disagree, error, analysed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    db.ids[rel],
                    res["bpm"],
                    res["reliability"],
                    res["agree"],
                    res["windows"],
                    res["prominence"],
                    " ".join(map(str, res["tempos"])),
                    1 if rel in disagree else 0,
                    error,
                    at,
                )
                for rel, (res, error) in sorted(results.items())
            ],
        )
    counts = defaultdict(int)
    for res, _e in results.values():
        counts[res["reliability"]] += 1
    print(
        f"bpm_analysis: {dict(counts)}, {len(disagree)} files whose copies"
        " disagree by more than 10 BPM"
    )


def stored(db: TagDb) -> list[dict]:
    # The rows of bpm_analysis with the file's path.
    db.con.executescript(BPM_ANALYSIS_SCHEMA)
    columns = ("bpm", "reliability", "agree", "windows")
    return [
        {"path": db.path_of[fid], **dict(zip(columns, row))}
        for fid, *row in db.con.execute(
            "SELECT file_id, bpm, reliability, agree, windows FROM bpm_analysis"
            " ORDER BY file_id"
        )
    ]


def record(r: dict, tag: str, old: str | None, new: str) -> dict:
    return {
        "path": r["path"],
        "tag": tag,
        "old": [old],
        "new": [new],
        "reliability": r["reliability"],
        "agree": f'{r["agree"]}/{r["windows"]}',
        "source": "detect_bpm.py",
    }


def apply(db: TagDb) -> None:
    todo = [
        r
        for r in stored(db)
        if r["reliability"] in ("high", "medium") and r["bpm"] > 0
    ]
    if not todo:
        sys.exit("nothing to apply: run analyse first")
    backup = db.backup("bpm-detect")
    db.journal(
        "bpm-detect",
        [
            record(r, "BPM", first(db, r["path"], "BPM") or None, str(r["bpm"]))
            for r in todo
        ],
    )  # journal first
    values = {r["path"]: [str(r["bpm"])] for r in todo}
    db.write(list(values), lambda path: {"BPM": values[path]})
    bad = [
        p
        for p, v in values.items()
        if db.tags[p].get("BPM") != v
        or [
            x[0]
            for x in db.con.execute(
                "SELECT value FROM tag WHERE file_id = ? AND name = 'BPM' ORDER"
                " BY pos",
                (db.ids[p],),
            )
        ]
        != v
    ]
    left0 = db.con.execute(
        "SELECT COUNT(*) FROM tag WHERE name = 'BPM' AND value = '0'"
    ).fetchone()[0]
    print(
        f"stored {len(todo)} BPM values ({len(bad)} failed verification);"
        f" {left0} files still 0; backup {backup}"
    )


def reliability(db: TagDb) -> None:
    rows = [r for r in stored(db) if r["bpm"] > 0]
    if not rows:
        sys.exit("nothing to apply: run analyse first")
    backup = db.backup("bpm-reliability")
    changes: dict[str, dict[str, list[str]]] = {}  # path -> {tag: values}
    records = []
    for r in rows:
        wanted = {"BPM_RELIABILITY": str(LEVEL[r["reliability"]])}
        if r["reliability"] == "low":
            wanted["BPM"] = str(r["bpm"])
        for tag, new in wanted.items():
            old = first(db, r["path"], tag) or None
            if old != new:
                changes.setdefault(r["path"], {})[tag] = [new]
                records.append(record(r, tag, old, new))
    db.journal("bpm-detect", records)  # journal first
    db.write(list(changes), lambda path: changes[path])
    bad = sum(
        1
        for path, tags in changes.items()
        for tag, v in tags.items()
        if [
            x[0]
            for x in db.con.execute(
                "SELECT value FROM tag WHERE file_id = ? AND name = ? ORDER"
                " BY pos",
                (db.ids[path], tag),
            )
        ]
        != v
    )
    counts = db.con.execute(
        "SELECT value, COUNT(*) FROM tag WHERE name = 'BPM_RELIABILITY' GROUP"
        " BY value ORDER BY value"
    ).fetchall()
    zero = db.con.execute(
        "SELECT COUNT(*) FROM tag WHERE name = 'BPM' AND value = '0'"
    ).fetchone()[0]
    print(
        f"{len(records)} values written ({bad} failed verification);"
        f" BPM_RELIABILITY counts {counts}; BPM still 0: {zero}; backup"
        f" {backup}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Detect the tempo of every file whose BPM is 0 and store it in the"
            " database."
        )
    )
    parser.add_argument(
        "--database",
        "-d",
        required=True,
        metavar="DB",
        help="the library database",
    )
    parser.add_argument("command", choices=("analyse", "apply", "reliability"))
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="(analyse) only the first N files",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=12,
        metavar="N",
        help="(analyse) files analysed in parallel (default: 12)",
    )
    args = parser.parse_args()
    if not os.path.exists(args.database):
        print(
            f"{args.database}: no such database (create it with"
            " scripts/initialize-database)",
            file=sys.stderr,
        )
        return 1
    db = TagDb(args.database)
    if args.command == "analyse":
        analyse(db, args.limit, args.jobs)
    elif args.command == "apply":
        apply(db)
    else:
        reliability(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
