#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Copies the music library to the phone over adb (USB debugging).
#
#     copy_library.py --desktop-db DB [--dest /storage/<card id>/MusicLibrary]
#     [--jobs 3] [--dry-run] [--verify-only]
#
# Copies every file listed in the desktop database and the cover images
# (jpg, jpeg, png) under the music root, in
# path order.  It can be stopped and started again: files already on the phone
# with the same size are skipped.  The card
# (exFAT) cannot hold some names, so the names are cleaned the same way the
# phone app cleans them (clean_path below and
# cleanPath in android/engine.cpp must stay identical):
#
#   * each of  * ? : " < > |  \  and control characters becomes _
#   * trailing dots and spaces of a file or folder name become _ (the card drops
#     a trailing dot, which would break the name)
#   * names that differ only in capital letters are one name on the card: the
#     file that is not the primary is skipped
#
# The database is not changed; it is synchronised with the phone by
# sync_phone.py.
from __future__ import annotations

import sys

# a program of its own: it leaves no .pyc files behind
sys.dont_write_bytecode = True

import argparse
import os
import sqlite3
from phone_adb import adb, on_phone
from phone_files import copy_files, manifest


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Copy the music library to the phone's SD card (or any folder on"
            " the phone) over adb."
        )
    )
    ap.add_argument(
        "--desktop-db", required=True, help="the desktop database"
    )
    ap.add_argument("--dest", default="/storage/2DDD-F0CA/MusicLibrary")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument(
        "--database", action="store_true", help="(use sync_phone.py)"
    )
    args = ap.parse_args()
    db_path = os.path.abspath(args.desktop_db)
    music_root = (
        sqlite3.connect(db_path)
        .execute("SELECT value FROM meta WHERE key = 'root'")
        .fetchone()[0]
    )

    if args.database:
        print(
            "Use sync_phone.py: it first takes over the plays and ratings made"
            " on the phone, which a plain copy would overwrite."
        )
        return 1

    files, skipped = manifest(db_path, music_root)
    total = sum(s for _f, _r, s in files)
    print(
        f"{len(files)} files, {total / 1e9:.1f} GB; skipped {len(skipped)}",
        flush=True,
    )
    for line in skipped:
        print("  skipped:", line, flush=True)
    try:
        have = on_phone(args.dest)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    todo = [f for f in files if have.get(f[1].lower()) != f[2]]
    print(
        f"already on the phone: {len(files) - len(todo)}; to copy: {len(todo)}"
        f" ({sum(s for _f, _r, s in todo) / 1e9:.1f} GB)",
        flush=True,
    )
    if args.dry_run or args.verify_only:
        if args.verify_only:
            missing = [r for _f, r, s in files if have.get(r.lower()) != s]
            print(
                f"verify: {len(files) - len(missing)} of {len(files)} files"
                " present with the right size; missing or wrong:"
                f" {len(missing)}"
            )
            for r in missing[:20]:
                print("  ", r)
            return 0 if not missing else 1
        return 0

    def push_adb(source, rel):
        adb("push", source, f"{args.dest}/{rel}")

    return copy_files(todo, push_adb, args.jobs)


if __name__ == "__main__":
    sys.exit(main())
