#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Points the library database at the new location of the music folder.
#
# The database records the music folder once (meta.root) and every track as a
# path relative to it.  After the music has been moved, this updates that one
# record.  It checks how many of the database's tracks exist under the new
# folder, refuses a folder that holds none of them, and copies the database
# to <database>.bak-before-change-music-root first.  Close the player and the
# editor before running it.
from __future__ import annotations

import argparse
import os
import sys

from tagdb import TagDb


def say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Point the library database at the new location of the "
        "music folder."
    )
    parser.add_argument(
        "--database",
        "-d",
        required=True,
        metavar="DB",
        help="the library database",
    )
    parser.add_argument(
        "--root",
        "-r",
        required=True,
        metavar="DIR",
        help="the new location of the music folder",
    )
    args = parser.parse_args()

    if not os.path.exists(args.database):
        say(f"{args.database}: no such database")
        say("  (create it with scripts/initialize-database)")
        return 1
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        say(f"error: not a directory: {root}")
        return 1

    db = TagDb(args.database)
    old = db.root()
    if old == root:
        say(f"The music folder is already {root}: nothing to do.")
        return 0
    found = sum(
        1 for path in db.ids if os.path.exists(os.path.join(root, path))
    )
    total = len(db.ids)
    if total and found == 0:
        say(f"error: none of the {total} tracks of the database exist under")
        say(f"{root}; the database was not changed.")
        say("  The tracks' paths are relative to the music folder: it must")
        say("  hold the same folders as before (for example cd-music/...).")
        return 1
    backup = db.backup("change-music-root")
    with db.con:
        db.con.execute("DELETE FROM meta WHERE key = 'root'")
        db.con.execute("INSERT INTO meta VALUES ('root', ?)", (root,))
    print(f"music folder: {old or '(none recorded)'} -> {root}")
    print(f"{found} of {total} tracks exist there ({total - found} missing)")
    print(f"backup: {backup}")
    if found < total:
        print("The editor's File > Scan for New Files lists the missing ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
