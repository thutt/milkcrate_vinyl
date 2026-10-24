# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Measures the loudness of audio files (EBU R128) for volume leveling.
#
# measure(path) -> (integrated loudness in LUFS, true peak in dBFS), or None
# when the file could not be measured.
# Run as a script it measures every file of the database whose loudness is not
# set and stores loudness and peak in
# media_file (library data, like play_count):
#
#   loudness.py measure [--limit N] [--match TEXT]     write
#   json/loudness-results.csv (no database change)
#   loudness.py apply                                  back up, journal, store
#   the results, verify
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from loudness_measure import measure
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
JSON = os.path.join(HERE, "..", "json")
DB = os.path.join(JSON, "tags.db")
RESULTS = os.path.join(JSON, "loudness-results.csv")


def _one(args):
    root, rel = args
    try:
        return rel, measure(os.path.join(root, rel))
    except Exception:
        return rel, None


def run_measure(limit: int | None, match: str | None) -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    root = con.execute("SELECT value FROM meta WHERE key = 'root'").fetchone()[
        0
    ]
    paths = [
        r[0]
        for r in con.execute(
            "SELECT path FROM media_file WHERE loudness IS NULL ORDER BY path"
        )
    ]
    if match:
        paths = [p for p in paths if match.lower() in p.lower()]
    if limit:
        paths = paths[:: max(1, len(paths) // limit)][:limit]
    print(f"{len(paths)} files to measure", file=sys.stderr)
    start, rows = time.time(), []
    with ProcessPoolExecutor(max_workers=12) as pool:
        for n, (rel, result) in enumerate(
            pool.map(_one, [(root, p) for p in paths], chunksize=4), 1
        ):
            rows.append((rel, result))
            if n % 500 == 0 or n == len(paths):
                print(
                    f"  {n}/{len(paths)}  {time.time() - start:.0f}s",
                    file=sys.stderr,
                )
    with open(RESULTS, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["path", "loudness", "peak"])
        for rel, result in rows:
            w.writerow([
                rel,
                "" if result is None else result[0],
                "" if result is None else result[1],
            ])
    print(
        f"{RESULTS}: {sum(r is not None for _p, r in rows)} measured,"
        f" {sum(r is None for _p, r in rows)} failed"
    )


def apply() -> None:
    rows = [
        r
        for r in csv.DictReader(open(RESULTS, encoding="utf-8"))
        if r["loudness"] != ""
    ]
    con = sqlite3.connect(DB, timeout=60)
    backup = f"{DB}.bak-before-loudness"
    if os.path.exists(backup):
        sys.exit(f"{backup} already exists")
    out = sqlite3.connect(backup)
    with out:
        con.backup(out)
    out.close()
    old = {
        p: (l, k)
        for p, l, k in con.execute(
            "SELECT path, loudness, peak FROM media_file"
        )
    }
    at = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(
        os.path.join(JSON, "loudness-journal.jsonl"), "a", encoding="utf-8"
    ) as j:  # journal first
        for r in rows:
            j.write(
                json.dumps({
                    "at": at,
                    "path": r["path"],
                    "old": list(old[r["path"]]),
                    "new": [float(r["loudness"]), float(r["peak"])],
                    "source": "loudness.py (ffmpeg ebur128)",
                })
                + "\n"
            )
    with con:
        con.executemany(
            "UPDATE media_file SET loudness = ?, peak = ? WHERE path = ?",
            [(float(r["loudness"]), float(r["peak"]), r["path"]) for r in rows],
        )
    bad = sum(
        1
        for r in rows
        if con.execute(
            "SELECT loudness, peak FROM media_file WHERE path = ?", (r["path"],)
        ).fetchone()
        != (float(r["loudness"]), float(r["peak"]))
    )
    left = con.execute(
        "SELECT COUNT(*) FROM media_file WHERE loudness IS NULL"
    ).fetchone()[0]
    print(
        f"stored {len(rows)} measurements ({bad} failed verification); {left}"
        f" files not measured; backup {backup}"
    )


if __name__ == "__main__":
    if sys.argv[1] == "measure":
        limit = (
            int(sys.argv[sys.argv.index("--limit") + 1])
            if "--limit" in sys.argv
            else None
        )
        match = (
            sys.argv[sys.argv.index("--match") + 1]
            if "--match" in sys.argv
            else None
        )
        run_measure(limit, match)
    else:
        apply()
