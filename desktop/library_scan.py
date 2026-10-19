# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Finds new, missing and changed files under the music folder.
#
# Nothing here scans by itself: the editor and the player call scan() when the
# user asks.  The database is
# changed only through TagDb.add_files / remove_files / reread_files.
#
# Cleanup applied to every new file (the same rules the existing library got):
#   * TRACKNUMBER values are integers ("01" -> "1", "01/03" -> "1/3")
#   * a file without a BPM gets BPM "0" (unknown)
#   * optionally, a file without an ALBUM gets the name of its folder
from __future__ import annotations

import os
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import tagio
from schema import normalize_track_number

MTIME_TOLERANCE = (
    1.0  # seconds; file systems differ in how exactly they keep the time
)


def read_one(path: str) -> dict:
    # The size, modification time and tags of one media file, or {"error": ...}
    # if it cannot be read.
    try:
        stat = os.stat(path)
        return {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "tags": tagio.read_tags(path),
        }
    except (OSError, tagio.TagError) as e:
        return {"error": str(e) or type(e).__name__}


@dataclass
class Scan:
    root: str
    new: list[str] = field(
        default_factory=list
    )  # relative paths of files not in the database
    missing: list[str] = field(
        default_factory=list
    )  # in the database but no longer under the root
    changed: list[str] = field(
        default_factory=list
    )  # in both, but size or modification time differ
    unchanged: int = 0


def scan(db, root: str, progress=None) -> Scan:
    # Compare the media files under root with the database.  progress(n) is
    # called now and then and may
    # return False to cancel (then None is returned).
    known = {
        path: (size, mtime)
        for path, size, mtime in db.con.execute(
            "SELECT path, size, mtime FROM media_file"
        )
    }
    result = Scan(root)
    seen: set[str] = set()
    for n, full in enumerate(tagio.find_media(root), 1):
        if progress and n % 200 == 0 and progress(n) is False:
            return None
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        seen.add(rel)
        if rel not in known:
            result.new.append(rel)
            continue
        size, mtime = known[rel]
        try:
            stat = os.stat(full)
        except OSError:
            result.missing.append(rel)
            continue
        if (
            stat.st_size != size
            or mtime is None
            or abs(stat.st_mtime - mtime) > MTIME_TOLERANCE
        ):
            result.changed.append(rel)
        else:
            result.unchanged += 1
    result.missing += sorted(set(known) - seen - set(result.missing))
    result.missing.sort()
    return result


def read_files(
    root: str, rels: list[str], jobs: int = 4, progress=None
) -> dict[str, dict] | None:
    # Read the tags of these files: {relative path: {"size", "mtime", "tags"} or
    # {"error"}}.  progress(n) as for scan().
    entries: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for n, (rel, entry) in enumerate(
            zip(
                rels, pool.map(read_one, [os.path.join(root, r) for r in rels])
            ),
            1,
        ):
            entries[rel] = entry
            if progress and progress(n) is False:
                return None
    return entries


def clean(
    rel: str, entry: dict, album_from_folder: bool = True
) -> tuple[dict[str, list[str]], list[str]]:
    # The tags the file gets in the database, and what the cleanup did (for
    # display).
    tags = {k: list(v) for k, v in (entry.get("tags") or {}).items()}
    notes: list[str] = []
    if "error" in entry:
        return tags, notes
    if "TRACKNUMBER" in tags:
        fixed = [normalize_track_number(v) for v in tags["TRACKNUMBER"]]
        if fixed != tags["TRACKNUMBER"]:
            tags["TRACKNUMBER"] = fixed
            notes.append("track number as integer")
    if not any(v.strip() for v in tags.get("BPM", [])):
        tags["BPM"] = ["0"]
        notes.append("BPM 0")
    folder = os.path.basename(os.path.dirname(rel))
    if (
        album_from_folder
        and folder
        and not any(v.strip() for v in tags.get("ALBUM", []))
    ):
        tags["ALBUM"] = [folder]
        notes.append(f"ALBUM from folder: {folder}")
    return tags, notes


# -- duplicates among new files and the library --


def _key(tags: dict[str, list[str]], normalize) -> tuple[str, str] | None:
    title = next((v for v in tags.get("TITLE", []) if v.strip()), "")
    artist = " ".join(v for v in tags.get("ARTIST", []) if v.strip())
    if not title or not artist:
        return None
    return normalize(artist), normalize(title)


def duplicate_groups(db, new_paths: list[str], normalize) -> list[dict]:
    # Groups of files with the same artist and title that include at least one
    # of the new files, and whose duplicate
    # marks differ from what the library's rule gives: the FLAC is the primary;
    # ties go to the newer file, then the larger,
    # then a random one.  Each group: {"primary": path, "copies": [paths],
    # "random": bool, "marks": {path: primary}}
    # where marks holds only the files whose mark would change.
    by_key: dict[tuple, list[str]] = {}
    for path, tags in db.tags.items():
        key = _key(tags, normalize)
        if key:
            by_key.setdefault(key, []).append(path)
    info = {
        p: (s, m)
        for p, s, m in db.con.execute(
            "SELECT path, size, mtime FROM media_file"
        )
    }
    wanted = set(new_paths)
    groups = []
    for members in by_key.values():
        if len(members) < 2 or not wanted & set(members):
            continue

        def rank(p: str):
            size, mtime = info[p]
            return (p.lower().endswith(".flac"), mtime or 0, size or 0)

        best = max(rank(p) for p in members)
        tied = [p for p in members if rank(p) == best]
        primary = tied[0] if len(tied) == 1 else random.choice(sorted(tied))
        marks = {
            p: primary
            for p in members
            if p != primary and db.dup_of.get(p) != primary
        }
        if db.dup_of.get(primary) is not None:
            marks[primary] = None
        for p in list(
            marks
        ):  # copies of a file that stops being a primary follow the new primary
            if marks[p] is not None:
                for c in db.copies.get(p, []):
                    if c != primary and db.dup_of.get(c) != primary:
                        marks[c] = primary
        if marks:
            groups.append({
                "primary": primary,
                "copies": sorted(p for p in members if p != primary),
                "random": len(tied) > 1,
                "marks": marks,
            })
    groups.sort(key=lambda g: g["primary"].lower())
    return groups
