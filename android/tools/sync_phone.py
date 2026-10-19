#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Command-line sync of the desktop and the phone.
#
#     sync_phone.py [--code CODE] [--host ADDRESS] [--no-music] [--force]
#     over Wi-Fi (or USB tethering): no USB debugging needed
#     sync_phone.py --adb [--no-music] [--force]
#     over a USB cable with USB debugging on
#
# On the phone open the app, Settings, "Sync with the desktop", Start.  It shows
# a pairing code; give it with --code once
# (it is remembered in phone-link.json next to the desktop database until the
# phone's "New code").  The
# phone is found by itself on the Wi-Fi
# (a UDP search); --host is for a network where that does not work.
#
#   1. copies the phone's tags.db to the desktop
#   2. takes over what the phone changed: play counts, last played times and
#      ratings (the newer write of each track wins,
#      media_file.stats_modified), and the dynamic-playlist entries it played
#      (they leave the list and are replaced)
#   3. sends the merged database to the phone (the desktop copy is backed up
#      first as <database>.bak-before-phone-sync);
#      if the phone played something since step 1 it says so and steps 1 to 3
#      are done again
#   4. copies new music to the card (skip with --no-music)
#
# Close the desktop player and editor first: they keep the database in memory
# and would overwrite the merge.
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

import phone_adb  # the adb command
import phone_files  # what to copy, how
import phone_link
import phone_sync

PACKAGE = "org.musicplayer.player"
DB_PATH = ""  # the desktop database (--desktop-db)
LINK_FILE = ""  # phone-link.json, in the same folder


def desktop_programs_running() -> list[str]:
    r = subprocess.run(
        ["pgrep", "-af", r"tagdb_editor\.py|player\.py"],
        capture_output=True,
        text=True,
    )
    return [line for line in r.stdout.splitlines() if "sync_phone" not in line]


def merge_phone_copy(pulled: str | None, tmp: str, first: bool) -> str | None:
    # Merge the phone's database (None: it has none) into the desktop one;
    # returns a consistent snapshot of the result.
    import tagdb

    db = tagdb.TagDb(DB_PATH)
    snapshot, report = phone_sync.merge_and_snapshot(
        db, pulled, tmp, DB_PATH + ".bak-before-phone-sync" if first else None
    )
    db.con.close()
    print(
        "From the phone:",
        (
            report.text()
            if pulled
            else "No database on the phone yet; it gets the desktop one."
        ),
    )
    return None if report.problems else snapshot


def music_todo(have: dict[str, int]):
    music_root = (
        sqlite3.connect(DB_PATH)
        .execute("SELECT value FROM meta WHERE key = 'root'")
        .fetchone()[0]
    )
    files, skipped = phone_files.manifest(DB_PATH, music_root)
    print(
        f"{len(files)} files in the library ({len(skipped)} skipped as"
        " same-name duplicates)"
    )
    todo = [f for f in files if have.get(f[1].lower()) != f[2]]
    print(
        f"already on the phone: {len(files) - len(todo)}; to copy: {len(todo)}"
        f" ({sum(s for _f, _r, s in todo) / 1e9:.1f} GB)",
        flush=True,
    )
    return todo


def load_recent() -> list[dict]:
    import json

    if not os.path.exists(LINK_FILE):
        return []
    saved = json.load(open(LINK_FILE))
    if "code" in saved and "recent" not in saved:  # the older file
        return [{"code": saved["code"], "name": ""}]
    return saved.get("recent", [])


def sync_network(args) -> int:
    import json

    recent = load_recent()
    codes = (
        [args.code]
        if args.code
        else [r["code"] for r in recent]
        or [input("Pairing code shown on the phone: ").strip()]
    )
    link, error = None, None
    for code in codes:  # the newest first: the one that fits the phone found
        try:
            link = phone_link.find_phone(code, args.host or None)
            break
        except phone_link.LinkError as e:
            error = error or e
    if link is None:
        print(error)
        return 1
    info = link.info
    print(
        f"Phone {info.get('name', '')} found at {link.host} (music folder"
        f" {info['root']}, {info['free'] / 1e9:.1f} GB free)."
    )
    with tempfile.TemporaryDirectory() as tmp:
        for attempt in range(1, 4):
            pulled = os.path.join(tmp, "phone.db")
            try:
                generation = link.get_database(pulled)
            except phone_link.NoDatabase:  # a new phone
                pulled, generation = None, 0
            snapshot = merge_phone_copy(pulled, tmp, first=attempt == 1)
            if snapshot is None:
                return 1
            try:
                link.put_database(snapshot, generation)
                break
            except phone_link.Conflict:
                print(
                    "The phone recorded a play meanwhile; fetching its database"
                    " again."
                )
        else:
            print(
                "The phone kept changing its database; try again when it is not"
                " playing."
            )
            return 1
    print("Database on the phone: merged and current.")
    with open(LINK_FILE, "w") as f:
        json.dump(
            {
                "recent": phone_link.remember(
                    recent, link.code, info.get("name", "")
                )
            },
            f,
        )
    if not args.no_music:
        print("Music:")
        todo = music_todo({k.lower(): v for k, v in link.files().items()})
        if todo and phone_files.copy_files(
            todo, lambda source, rel: link.put_file(source, rel), args.jobs
        ):
            return 1
    link.done()
    return 0


def sync_adb(args) -> int:
    try:
        phone_adb.adb_program()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    adb = phone_adb.adb
    if adb("get-state", check=False).strip() != "device":
        print(
            "The phone is not connected (USB debugging on, cable in, allow this"
            " computer)."
        )
        return 1
    remote = f"{args.dest}/tags.db"
    adb(
        "shell", f"am force-stop {PACKAGE}"
    )  # the app must not be writing while we copy
    with tempfile.TemporaryDirectory() as tmp:
        pulled = os.path.join(tmp, "phone.db")
        have_remote = (
            adb("shell", f"ls '{remote}' 2>/dev/null", check=False).strip()
            == remote
        )
        if have_remote:
            adb("pull", remote, pulled)
            size = int(adb("shell", f"stat -c %s '{remote}'").strip())
            if os.path.getsize(pulled) != size:
                print(
                    "The copy from the phone is incomplete; nothing was"
                    " changed."
                )
                return 1
        snapshot = merge_phone_copy(
            pulled if have_remote else None, tmp, first=True
        )
        if snapshot is None:
            return 1
        adb("shell", f"mkdir -p '{args.dest}'")
        adb("push", snapshot, remote + ".new")
        pushed = int(adb("shell", f"stat -c %s '{remote}.new'").strip())
        if pushed != os.path.getsize(snapshot):
            print(
                "The copy to the phone is incomplete; the phone keeps its old"
                " database."
            )
            return 1
        adb("shell", f"mv -f '{remote}.new' '{remote}'")
        print(
            f"Database on the phone: {pushed / 1e6:.1f} MB, merged and current."
        )
    if not args.no_music:
        print("Music:")
        r = subprocess.run([
            sys.executable,
            "-B",
            os.path.join(HERE, "copy_library.py"),
            "--dest",
            args.dest,
            "--jobs",
            "3",
            "--desktop-db",
            DB_PATH,
        ])
        return r.returncode
    return 0


def main() -> int:
    global DB_PATH, LINK_FILE
    ap = argparse.ArgumentParser(
        description="Synchronise the desktop and the phone."
    )
    ap.add_argument(
        "--code", help="the pairing code shown on the phone (remembered)"
    )
    ap.add_argument(
        "--host", help="the phone's address (found by itself on the Wi-Fi)"
    )
    ap.add_argument(
        "--adb",
        action="store_true",
        help="use a USB cable with USB debugging instead of the network",
    )
    ap.add_argument(
        "--dest",
        default="/storage/2DDD-F0CA/MusicLibrary",
        help="(--adb) the library folder on the phone",
    )
    ap.add_argument(
        "--jobs", type=int, default=2, help="files sent at the same time"
    )
    ap.add_argument("--no-music", action="store_true", help="only the database")
    ap.add_argument(
        "--desktop-db", required=True, help="the desktop database"
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="do not stop for a running desktop player or editor",
    )
    args = ap.parse_args()
    DB_PATH = os.path.abspath(args.desktop_db)
    LINK_FILE = os.path.join(os.path.dirname(DB_PATH), "phone-link.json")
    running = desktop_programs_running()
    if running and not args.force:
        print(
            "Close the desktop player and editor first (they would overwrite"
            " the merged database):"
        )
        print("\n".join("  " + r[:120] for r in running))
        return 1
    return sync_adb(args) if args.adb else sync_network(args)


if __name__ == "__main__":
    sys.exit(main())
