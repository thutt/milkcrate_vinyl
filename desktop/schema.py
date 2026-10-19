# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The library database schema and the track number cleanup.
#
# Schema:
#   media_file(id, path UNIQUE, size, mtime, read_error,   one row per media
#   file, path relative to the music root (meta.root);
#              play_count, last_played, duplicate_of, rating)  the last four are
#              library data, not tags (never written to the media files)
#   tag(file_id, name, pos, value)                         one row per tag
#   value; any tag name may repeat for a file,
#                                                          pos (0, 1, ...) keeps
#                                                          the values in their
#                                                          original order
#   playlist(id, name, created, rules, played, kind, ...)  playlists, dynamic
#   playlists, containers and sequences (library data)
#   playlist_item(id, playlist_id, pos, file_id)           the entries of a
#   playlist, in order; a file may appear more than once
#   playlist_member(id, parent_id, pos, child_id)          a container's
#   playlists and a sequence's containers
#   meta(key, value)                                       the music root, read
#   time, schema version, dynamic list length
#   (media_file also has loudness and peak, measured by loudness.py; library
#   data like play_count)
#
# Reading the tags back:  SELECT name, value FROM tag WHERE file_id = ? ORDER BY
# name, pos;
# initialize_database.py creates a database with this schema.
from __future__ import annotations

import re

# The playlist table's columns (a function of the table name, because TagDb
# rebuilds the table of an older database).
# A name is unique within its own kind of list: playlists (manual and dynamic),
# containers and sequences have separate
# name spaces (see PLAYLIST_NAME_INDEX).
PLAYLIST_COLUMNS = """
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    created TEXT,                                               -- UTC, ISO 8601
    -- NULL = a manual playlist; else a dynamic playlist's rules (JSON)
    rules   TEXT,
    -- dynamic: ids of files played since it was generated (JSON)
    played  TEXT NOT NULL DEFAULT '[]',
    -- manual, dynamic (rules set), container or sequence
    kind    TEXT NOT NULL DEFAULT 'manual',
    -- container: play for this long, then move on; NULL = no limit
    limit_seconds INTEGER,
    -- container, at the limit: fade (out over a few seconds), cut or
    -- finish (the track)
    end_mode      TEXT NOT NULL DEFAULT 'fade',
    -- container, if its playlists run out first: repeat them or go on to
    -- the next
    when_empty    TEXT NOT NULL DEFAULT 'repeat'
"""
PLAYLIST_NAME_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS playlist_name
    ON playlist(
        CASE WHEN kind IN ('container', 'sequence') THEN kind
             ELSE 'playlist' END,
        name);
"""

# Created by the build and, if missing, by TagDb when it opens an older (version
# 3) database.
PLAYLIST_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS playlist ({PLAYLIST_COLUMNS});
-- a container's playlists and a sequence's containers, in order (a child may
-- appear more than once in a sequence)
CREATE TABLE IF NOT EXISTS playlist_member (
    id        INTEGER PRIMARY KEY,
    parent_id INTEGER NOT NULL REFERENCES playlist(id) ON DELETE CASCADE,
    pos       INTEGER NOT NULL,
    child_id  INTEGER NOT NULL REFERENCES playlist(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS playlist_member_order
    ON playlist_member(parent_id, pos);
CREATE TABLE IF NOT EXISTS playlist_item (
    id          INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlist(id) ON DELETE CASCADE,
    -- 0, 1, ... order within the playlist
    pos         INTEGER NOT NULL,
    file_id     INTEGER NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    -- the phone marks a dynamic-playlist entry it has played; the
    -- desktop removes it at the next sync
    removed_at  TEXT
);
CREATE INDEX IF NOT EXISTS playlist_item_order
    ON playlist_item(playlist_id, pos);
"""

# Tempo estimates made by bpm_batch.py (one row per analysed file), kept until
# they are applied.  Created by the tool in
# an older database too.
BPM_ANALYSIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS bpm_analysis (
    file_id         INTEGER PRIMARY KEY
                    REFERENCES media_file(id) ON DELETE CASCADE,
    -- the estimate; 0 = none
    bpm             INTEGER NOT NULL,
    -- low, medium or high
    reliability     TEXT NOT NULL,
    -- analysis windows that agree
    agree           INTEGER NOT NULL,
    -- analysis windows used
    windows         INTEGER NOT NULL,
    prominence      REAL NOT NULL,
    -- the candidate tempos, space separated
    tempos          TEXT NOT NULL,
    -- 1 if copies of the same song got tempos more than 10 BPM apart
    copies_disagree INTEGER NOT NULL DEFAULT 0,
    -- why the file could not be analysed
    error           TEXT NOT NULL DEFAULT '',
    analysed_at     TEXT NOT NULL                               -- UTC, ISO 8601
);
"""


SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE media_file (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,
    size       INTEGER,
    mtime      REAL,
    read_error TEXT,
    -- times played by the player
    play_count   INTEGER NOT NULL DEFAULT 0,
    -- UTC, ISO 8601 ("2026-10-13T17:07:52Z"); NULL = never
    last_played  TEXT,
    -- the kept copy this file duplicates; NULL = not a duplicate
    duplicate_of INTEGER REFERENCES media_file(id) ON DELETE SET NULL,
    -- NULL = not rated; 0 to 5 inclusive
    rating       INTEGER CHECK (rating IS NULL OR rating BETWEEN 0 AND 5),
    -- integrated loudness in LUFS (EBU R128), measured from the audio;
    -- NULL = not measured
    loudness     REAL,
    -- true peak in dBFS; NULL = not measured
    peak         REAL,
    -- UTC, ISO 8601 with milliseconds: when play_count, last_played or
    -- rating was last written (the phone sync keeps the newer)
    stats_modified TEXT
);
CREATE TRIGGER rating_whole_insert BEFORE INSERT ON media_file
WHEN NEW.rating IS NOT NULL AND typeof(NEW.rating) <> 'integer'
BEGIN SELECT RAISE(ABORT,
    'rating must be a whole number 0 to 5, or NULL for not rated'); END;
CREATE TRIGGER rating_whole_update BEFORE UPDATE OF rating ON media_file
WHEN NEW.rating IS NOT NULL AND typeof(NEW.rating) <> 'integer'
BEGIN SELECT RAISE(ABORT,
    'rating must be a whole number 0 to 5, or NULL for not rated'); END;
CREATE TABLE tag (
    file_id INTEGER NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    pos     INTEGER NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (file_id, name, pos)
) WITHOUT ROWID;
CREATE INDEX tag_name_value ON tag(name, value);
""" + PLAYLIST_SCHEMA + PLAYLIST_NAME_INDEX + BPM_ANALYSIS_SCHEMA


def normalize_track_number(value: str) -> str:
    # Track numbers are integers: drop leading zeros from every run of digits
    # ("01" -> "1", "01/03" -> "1/3",
    # "00" -> "0").  Anything else in the value ("1.", "1 ") is left as it is.
    return (
        re.sub(r"\d+", lambda m: str(int(m.group())), value)
        if re.search(r"(?<!\d)0\d", value)
        else value
    )
