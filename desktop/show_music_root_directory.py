#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Prints the music folder (the root) that the library database records.
#
# The root is printed alone on standard output, so a script can use it.  The
# database is opened read-only and is not changed.  Exit status: 0 the root
# was printed and the folder exists; 1 no root could be read; 2 the root was
# printed but the folder does not exist (a note says so on standard error).
from __future__ import annotations

import sys

# a program of its own: it leaves no .pyc files behind
sys.dont_write_bytecode = True

import argparse
import os
import sqlite3


def say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the music folder recorded in the library database."
    )
    parser.add_argument(
        "--database",
        "-d",
        required=True,
        metavar="DB",
        help="the library database",
    )
    args = parser.parse_args()

    if not os.path.exists(args.database):
        say(f"{args.database}: no such database")
        say("  (create it with scripts/initialize-database)")
        return 1
    try:
        con = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
        row = con.execute(
            "SELECT value FROM meta WHERE key = 'root'"
        ).fetchone()
    except sqlite3.Error as e:
        say(f"{args.database}: cannot read the music folder: {e}")
        return 1
    if not row or not row[0]:
        say(f"{args.database}: no music folder is recorded")
        say("  (scripts/change-music-root-directory --root DIR records one)")
        return 1
    print(row[0])
    if not os.path.isdir(row[0]):
        say("note: that folder does not exist on this computer")
        say("  (scripts/change-music-root-directory --root DIR changes it)")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
