#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Creates the library database from the media files under a music folder.
#
# Reads the tags of every media file under --root (the files are never
# modified), applies the same cleanup the editor and
# player apply to files found by a scan (track numbers as integers, BPM 0 where
# there is none, ALBUM from the folder name
# where there is none), stores everything in a new database and marks duplicates
# (the same artist and title: the FLAC is
# kept, otherwise the newer, then the larger file).  Afterwards the player and
# the database editor are used to clean up
# the data; the editor's File > Scan for New Files adds files that appear later.
#
# Not done here (separate, slow steps): loudness for volume leveling
# (loudness.py) and tempo (bpm_batch.py).
# The database is never replaced by accident: an existing one is left alone
# unless --replace is given, and then it is
# copied to <database>.bak-before-init first.
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

import schema
import library_scan
import tagio
from tagdb import TagDb, normalize


def say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def backup_name(path: str) -> str:
    target = f"{path}.bak-before-init"
    n = 1
    while os.path.exists(target):
        n += 1
        target = f"{path}.bak-before-init-{n}"
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create the library database from the media files under a music"
            " folder."
        )
    )
    parser.add_argument(
        "--root",
        "-r",
        required=True,
        metavar="DIR",
        help="the music folder (every media file below it is read)",
    )
    parser.add_argument(
        "--database",
        "-d",
        required=True,
        metavar="DB",
        help="the database file to create",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=4,
        metavar="N",
        help="files to read in parallel (default: 4)",
    )
    parser.add_argument(
        "--no-album-from-folder",
        action="store_true",
        help=(
            "leave ALBUM empty where a file has none (default: use the folder"
            " name)"
        ),
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "replace an existing database (play counts, ratings and playlists"
            " are lost; a copy is kept)"
        ),
    )
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        parser.error(f"not a directory: {root}")
    database = os.path.abspath(args.database)
    if os.path.exists(database):
        if not args.replace:
            say(
                f"error: {database} already exists.  It holds play counts,"
                " ratings and playlists, so it is left alone."
            )
            say(
                "  To add new files to it, use the editor's File > Scan for New"
                " Files."
            )
            say(
                "  To start over, run this again with --replace (a copy is kept"
                " first)."
            )
            return 1
        copy = backup_name(database)
        shutil.copy2(database, copy)
        say(f"copied the existing database to {copy}")
        os.remove(database)
    os.makedirs(os.path.dirname(database), exist_ok=True)

    say(f"looking for media files under {root} ...")
    rels = sorted(
        os.path.relpath(p, root).replace(os.sep, "/")
        for p in tagio.find_media(root)
    )
    if not rels:
        say(f"error: no media files under {root}")
        return 1
    say(f"{len(rels)} media files; reading their tags ...")

    def progress(n: int) -> bool:
        if n % 500 == 0 or n == len(rels):
            say(f"  {n} of {len(rels)}")
        return True

    entries = library_scan.read_files(
        root, rels, jobs=max(1, args.jobs), progress=progress
    )

    con = sqlite3.connect(database)
    con.executescript(schema.SCHEMA)
    con.executemany(
        "INSERT INTO meta VALUES (?, ?)",
        [
            ("root", root),
            ("read_at", datetime.now(timezone.utc).isoformat()),
            ("schema_version", "7"),
        ],
    )
    con.commit()
    con.close()

    db = TagDb(database)
    prepared = {}
    for rel in rels:
        tags, _notes = library_scan.clean(
            rel, entries[rel], album_from_folder=not args.no_album_from_folder
        )
        prepared[rel] = (entries[rel], tags)
    say("storing the tags ...")
    db.add_files(prepared)

    say("looking for duplicates ...")
    groups = library_scan.duplicate_groups(db, rels, normalize)
    marks: dict[str, str | None] = {}
    for group in groups:
        marks.update(group["marks"])
    if marks:
        db.set_duplicates(marks)

    errors = sum(1 for e in entries.values() if "error" in e)
    tag_values = db.con.execute("SELECT COUNT(*) FROM tag").fetchone()[0]
    duplicates = db.con.execute(
        "SELECT COUNT(*) FROM media_file WHERE duplicate_of IS NOT NULL"
    ).fetchone()[0]
    db.con.execute("VACUUM")
    db.con.close()
    print(
        f"{database}: {len(rels)} files ({errors} unreadable), {tag_values} tag"
        f" values, {duplicates} marked as duplicates in {len(groups)} groups,"
        f" {os.path.getsize(database) / 1e6:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
