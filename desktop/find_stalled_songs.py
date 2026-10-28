#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Lists the songs that Qt's player does not start by itself.
#
# Such a song stays at 0:00 with no sound until the position is moved.  The
# files found so far all have a cover picture and an audio stream that starts
# after time 0 (the LAME or Xing header most MP3 encoders write).  This probes
# every track of the library with ffprobe, in parallel, for that pattern and
# prints the paths, relative to the music root, one per line.  The players
# work around the problem; this shows which songs it applies to.  The files
# and the database are not changed.  Needs ffprobe (part of ffmpeg).
from __future__ import annotations

import sys

# a program of its own: it leaves no .pyc files behind
sys.dont_write_bytecode = True

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor


def say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def stalls(full: str) -> bool | None:
    # True: a picture and a late-starting audio stream; None: not probed.
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                "stream=codec_type,start_time",
                full,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not streams:
        return None
    picture = any(s.get("codec_type") == "video" for s in streams)
    late = any(
        s.get("codec_type") == "audio" and float(s.get("start_time") or 0) > 0
        for s in streams
    )
    return picture and late


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List the songs that Qt's player does not start by "
        "itself: a cover picture and an audio stream starting after 0."
    )
    parser.add_argument(
        "--database",
        "-d",
        required=True,
        metavar="DB",
        help="the library database",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=os.cpu_count() or 4,
        metavar="N",
        help="files probed at the same time (default: the processors)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.database):
        say(f"{args.database}: no such database")
        say("  (create it with scripts/initialize-database)")
        return 1
    if shutil.which("ffprobe") is None:
        say("error: ffprobe is not installed (it is part of ffmpeg)")
        return 1
    con = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    row = con.execute("SELECT value FROM meta WHERE key = 'root'").fetchone()
    root = row[0] if row and row[0] else ""
    if not os.path.isdir(root):
        say(f"error: the music folder does not exist: {root or '(none)'}")
        say("  (scripts/show-music-root-directory, then")
        say("  scripts/change-music-root-directory --root DIR)")
        return 1
    paths = [
        r[0] for r in con.execute("SELECT path FROM media_file ORDER BY path")
    ]
    say(f"probing {len(paths)} files in {root} ...")
    found = unreadable = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = pool.map(lambda p: stalls(os.path.join(root, p)), paths)
        for n, (path, result) in enumerate(zip(paths, results), 1):
            if result is None:
                unreadable += 1
            elif result:
                found += 1
                print(path, flush=True)
            if n % 1000 == 0:
                say(f"  {n} of {len(paths)} probed, {found} found")
    say(f"{found} of {len(paths)} songs will not start by themselves")
    if unreadable:
        say(f"{unreadable} files could not be probed (missing or unreadable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
