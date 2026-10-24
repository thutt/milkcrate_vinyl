# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Lists the files to send to the phone and copies them in parallel.
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

LOST = 6  # failures in a row that mean the phone is gone
BAD = re.compile(r'[*?:"<>|\\\x00-\x1f]')


def clean_part(name: str) -> str:
    name = BAD.sub("_", name)
    stripped = name.rstrip(" .")
    return stripped + "_" * (len(name) - len(stripped))


def clean_path(path: str) -> str:
    return "/".join(clean_part(p) for p in path.split("/"))


def manifest(
    db_path: str, music_root: str
) -> tuple[list[tuple[str, str, int]], list[str]]:
    # [(source path, cleaned relative path, size)] in path order, and the
    # skipped files with the reason.
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT path, size, duplicate_of IS NULL FROM media_file ORDER BY path"
    ).fetchall()
    chosen: dict[str, tuple[str, int, bool]] = (
        {}
    )  # lower-case cleaned path -> (path, size, primary)
    skipped: list[str] = []
    for path, size, primary in rows:
        key = clean_path(path).lower()
        if key in chosen:
            keep = chosen[key]
            if primary and not keep[2]:  # the primary wins a collision
                skipped.append(f"{keep[0]}: same name as {path} on the card")
                chosen[key] = (path, size, bool(primary))
            else:
                skipped.append(f"{path}: same name as {keep[0]} on the card")
            continue
        chosen[key] = (path, size, bool(primary))
    files = [
        (os.path.join(music_root, p), clean_path(p), s)
        for p, s, _pr in chosen.values()
    ]
    for dirpath, _dirs, names in os.walk(music_root):
        for name in names:
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, music_root).replace(os.sep, "/")
                if clean_path(rel).lower() not in chosen:
                    files.append((full, clean_path(rel), os.path.getsize(full)))
                    chosen[clean_path(rel).lower()] = (rel, 0, False)
    files.sort(key=lambda f: f[1])
    return files, skipped


def copy_files(
    todo: list[tuple[str, str, int]],
    push_one,
    jobs: int,
    log=print,
    cancelled=lambda: False,
) -> int:
    # Copy [(source, relative path, size)] with push_one(source, rel) (raises on
    # failure; tried three times).  Stops starting files when cancelled() is
    # true, and after LOST failures in a row (the phone went away); a stopped
    # copy returns 1.
    skipped = 0
    lost = [0, False]  # failures in a row, gave up
    done = [0, 0]  # files, bytes
    failures: list[tuple[str, str]] = []
    lock = threading.Lock()
    start = time.time()
    folder = [None, -1]  # the folder of the furthest file started, and its
    # number: the files go in path order, but threads may start out of order

    def push(item, number):
        source, rel, size = item
        with lock:
            where = os.path.dirname(rel)
            if number > folder[1] and where != folder[0]:
                log(f"folder: {where or '.'}")
            if number > folder[1]:
                folder[0], folder[1] = where, number
        err = ""
        for attempt in (1, 2, 3):
            if cancelled() or lost[1]:
                return item, "not copied: stopped"
            try:
                push_one(source, rel)
                lost[0] = 0
                return item, None
            except Exception as e:  # adb or network: try again a little later
                err = str(e)
                for _ in range(2 * attempt * 10):  # sleep, but stay stoppable
                    if cancelled() or lost[1]:
                        break
                    time.sleep(0.1)
        with lock:
            lost[0] += 1
            if lost[0] >= LOST and not lost[1]:
                lost[1] = True
                log("The phone stopped answering; giving up.")
        return item, err

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(push, item, n) for n, item in enumerate(todo)]
        for n, future in enumerate(as_completed(futures), 1):
            (source, rel, size), err = future.result()
            with lock:
                if err and err.startswith("not copied"):
                    skipped += 1
                elif err:
                    failures.append((rel, err))
                    log(f"FAILED {rel}: {err[:200]}")
                else:
                    done[0] += 1
                    done[1] += size
                if n % 100 == 0 or n == len(todo):
                    rate = done[1] / max(time.time() - start, 1) / 1e6
                    left = sum(s for _f, _r, s in todo) - done[1]
                    eta = left / max(rate * 1e6, 1) / 60
                    log(
                        f"{n}/{len(todo)} files, {done[1] / 1e9:.1f} GB,"
                        f" {rate:.0f} MB/s, about {eta:.0f} min left,"
                        f" {len(failures)} failed"
                    )
    log(
        f"finished: {done[0]} copied, {len(failures)} failed, {skipped} left,"
        f" {(time.time() - start) / 60:.1f} min"
    )
    if cancelled():
        log("Stopped.")
    return 1 if failures or cancelled() else 0
