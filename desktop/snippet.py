# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Saves a snippet (a part of a track) as a file of its own.  The snippet has
# the file type of the track it is cut from (the audio is copied, not
# encoded again), is kept in the folder "Track Samples" of the music folder,
# is added to the database and to the playlist "Track Samples", which is made
# when it is missing.  The phone gets it with the next sync like any other
# file of the database.
#
# save(db, root, path, start_ms, end_ms) -> (path of the snippet, playlist id)
from __future__ import annotations

import os
import sqlite3
import subprocess

import library_scan
import loudness_measure
import tagio
from schema import RESERVED_PLAYLIST

SAMPLES_DIR = "Track Samples"
PLAYLIST = RESERVED_PLAYLIST
MIN_LENGTH_MS = 100


class SnippetError(Exception):
    pass


def _seconds(ms: int) -> int:
    return round(ms / 1000)


def clock(ms: int) -> str:
    return f"{_seconds(ms) // 60}:{_seconds(ms) % 60:02d}"


def _stamp(ms: int) -> str:
    # For a file name: no colon, which not every file system can hold.
    return f"{_seconds(ms) // 60}m{_seconds(ms) % 60:02d}s"


def _target(root: str, path: str, start_ms: int, end_ms: int) -> str:
    stem, ext = os.path.splitext(os.path.basename(path))
    name = f"{stem} (sample {_stamp(start_ms)}-{_stamp(end_ms)})"
    number = 1
    while os.path.exists(os.path.join(root, SAMPLES_DIR, name + ext)):
        number += 1
        name = f"{stem} (sample {_stamp(start_ms)}-{_stamp(end_ms)}) {number}"
    return f"{SAMPLES_DIR}/{name}{ext}"


def _cut(source: str, target: str, start_ms: int, end_ms: int) -> None:
    # The audio of the first audio stream, copied; the cover picture is left
    # out (some players cannot start a file that has both a cover picture and
    # a Xing header).  A FLAC file is encoded again, without loss: a copy
    # keeps the length of the whole song in the header of the file, and
    # players then show the wrong length.
    codec = "flac" if target.lower().endswith(".flac") else "copy"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            source,
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-map",
            "0:a:0",
            "-map_metadata",
            "0",
            "-c:a",
            codec,
            target,
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode or not os.path.exists(target):
        lines = proc.stderr.strip().splitlines()
        raise SnippetError(lines[-1] if lines else "ffmpeg failed")
    if os.path.getsize(target) == 0:
        raise SnippetError("The snippet came out empty.")


def _playlist(db) -> int:
    kinds = db.kinds()
    for pid, name, _count in db.playlists():
        if name == PLAYLIST and kinds.get(pid) == "manual":
            return pid
    return db.create_playlist(PLAYLIST, reserved=True)


def save(db, root: str, path: str, start_ms: int, end_ms: int):
    if end_ms - start_ms < MIN_LENGTH_MS:
        raise SnippetError("The snippet is empty: move the markers apart.")
    if path not in db.tags:
        raise SnippetError(f"{path} is not in the database.")
    os.makedirs(os.path.join(root, SAMPLES_DIR), exist_ok=True)
    rel = _target(root, path, start_ms, end_ms)
    full = os.path.join(root, rel)
    try:
        _cut(os.path.join(root, path), full, start_ms, end_ms)
        title = next(
            (v for v in db.tags[path].get("TITLE", []) if v.strip()),
            os.path.splitext(os.path.basename(path))[0],
        )
        # The snippet is not a copy of the song: a new title and album keep
        # it from being counted with the track it came from.
        tagio.write_tags(
            full,
            {
                "TITLE": [
                    f"{title} (sample {clock(start_ms)}-{clock(end_ms)})"
                ],
                "ALBUM": [SAMPLES_DIR],
                "TRACKNUMBER": None,
                "DISCNUMBER": None,
            },
        )
        entry = library_scan.read_one(full)
        tags, _notes = library_scan.clean(rel, entry, album_from_folder=False)
        db.add_files({rel: (entry, tags)})
    except (SnippetError, tagio.TagError, OSError, sqlite3.Error) as e:
        if os.path.exists(full):
            os.remove(full)
        raise SnippetError(str(e)) from e
    measured = loudness_measure.measure(full)
    if measured:
        db.set_loudness(rel, *measured)
    playlist = _playlist(db)
    db.add_to_playlist(playlist, [rel])
    return rel, playlist
