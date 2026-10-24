# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Tempo and loudness of selected files, stored in the database only (the media
# files are never touched).  Used by the editor and the player; bpm_batch.py
# does the same for the whole library.
#
#   run(function, root, paths, jobs, progress) -> {path: result} or None
#         function is bpm_one or loudness_one; progress(n) may return False to
#         cancel (then None is returned)
#   store_bpm(db, results) -> (paths whose BPM was set, paths left alone)
#   store_loudness(db, results) -> (paths measured, paths that failed)
#
# Each store backs up the database and writes a journal first, as the batch
# tools do.
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

from loudness_measure import measure
from schema import BPM_ANALYSIS_SCHEMA

NO_TEMPO = {
    "bpm": 0,
    "reliability": "low",
    "agree": 0,
    "windows": 0,
    "prominence": 0.0,
    "tempos": [],
}


def bpm_one(args):
    root, rel = args
    import detect_bpm

    try:
        return rel, detect_bpm.analyse(os.path.join(root, rel)), ""
    except Exception as e:  # undecodable file: stays 0
        return rel, dict(NO_TEMPO), str(e)[:120]


def loudness_one(args):
    root, rel = args
    try:
        return rel, measure(os.path.join(root, rel))
    except Exception:
        return rel, None


def run(function, root: str, paths: list[str], jobs: int, progress=None):
    results = {}
    with ProcessPoolExecutor(max_workers=max(1, jobs)) as pool:
        for n, item in enumerate(
            pool.map(function, [(root, p) for p in paths]), 1
        ):
            results[item[0]] = item[1:] if len(item) > 2 else item[1]
            if progress and progress(n) is False:
                pool.shutdown(wait=False, cancel_futures=True)
                return None
    return results


def store_bpm(db, results: dict):
    # results: {path: (analysis, error)} from bpm_one.  High and medium
    # reliability results become the BPM tag; low ones stay as they are.
    good = {
        p: r[0]["bpm"]
        for p, r in results.items()
        if r[0]["reliability"] in ("high", "medium") and r[0]["bpm"] > 0
    }
    left = [p for p in results if p not in good]
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.backup("bpm-detect")
    db.journal(
        "bpm-detect",
        [
            {
                "path": p,
                "tag": "BPM",
                "old": [(db.tags[p].get("BPM") or [None])[0]],
                "new": [str(bpm)],
                "reliability": results[p][0]["reliability"],
                "agree": (
                    f'{results[p][0]["agree"]}/{results[p][0]["windows"]}'
                ),
                "source": "file_analysis.py",
            }
            for p, bpm in good.items()
        ],
    )  # journal first
    db.write(list(good), lambda p: {"BPM": [str(good[p])]})
    with db.con:
        db.con.executescript(BPM_ANALYSIS_SCHEMA)
        db.con.executemany(
            "INSERT OR REPLACE INTO bpm_analysis(file_id, bpm, reliability,"
            " agree, windows, prominence, tempos, copies_disagree, error,"
            " analysed_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            [
                (
                    db.ids[p],
                    res["bpm"],
                    res["reliability"],
                    res["agree"],
                    res["windows"],
                    res["prominence"],
                    " ".join(map(str, res["tempos"])),
                    error,
                    at,
                )
                for p, (res, error) in sorted(results.items())
            ],
        )
    return list(good), left


def store_loudness(db, results: dict):
    # results: {path: (LUFS, peak) or None} from loudness_one.
    good = {p: r for p, r in results.items() if r}
    failed = [p for p, r in results.items() if not r]
    if good:
        db.backup("loudness")
        db.journal(
            "loudness",
            [
                {
                    "path": p,
                    "old": list(db.loud.get(p, (None, None))),
                    "new": list(r),
                    "source": "file_analysis.py (ffmpeg ebur128)",
                }
                for p, r in good.items()
            ],
        )  # journal first
        with db.con:
            db.con.executemany(
                "UPDATE media_file SET loudness = ?, peak = ? WHERE id = ?",
                [(r[0], r[1], db.ids[p]) for p, r in good.items()],
            )
        db.load_loudness()
    return list(good), failed
