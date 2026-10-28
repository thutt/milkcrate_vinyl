# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The library database held in memory, shared by the player and the editor.
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime

import dynamic_playlists
from schema import (
    PLAYLIST_COLUMNS,
    PLAYLIST_NAME_INDEX,
    PLAYLIST_SCHEMA,
    RESERVED_PLAYLIST,
    is_reserved_playlist_name,
    normalize_track_number,
)

STANDARD_TAGS = (
    "TITLE",
    "ARTIST",
    "ALBUM",
    "GENRE",
    "TRACKNUMBER",
    "DATE",
)  # a tag with only blank values counts as missing
SEPARATOR = "; "
SPLIT_TAGS = frozenset(
    {"ARTIST", "ALBUMARTIST", "GENRE", "SUBGENRE", "STYLE", "COMPOSER"}
)  # ";" separates values
NUMERIC_TAGS = frozenset({
    "TRACKNUMBER",
    "DISCNUMBER",
    "TOTALTRACKS",
    "TOTALDISCS",
    "DATE",
    "BPM",
    "BPM_RELIABILITY",
    "COMPILATION",
})
LIST_SEPARATORS = (
    ",",
    "/",
)  # in a multi-valued tag these usually mean one value should be several
MAX_CHOICES = (
    300  # tags with more distinct values than this are edited as plain text
)


def now_stamp() -> str:
    # The time for stats_modified: UTC, ISO 8601, milliseconds (strings of this
    # form compare in time order).
    t = time.time()
    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))
        + f".{int(t % 1 * 1000):03d}Z"
    )


def missing_tags(tags: dict[str, list[str]]) -> list[str]:
    return [
        k for k in STANDARD_TAGS if not any(v.strip() for v in tags.get(k, []))
    ]


def stat_text(stats: tuple, key: str) -> str:
    # How a file's rating ("@rating"), play count ("@plays") or last-played time
    # ("@last") is shown.
    rating, plays, last = stats
    if key == "@rating":
        return (
            ""
            if rating is None
            else "\u2605" * rating + "\u2606" * (5 - rating)
        )  # blank = not rated
    if key == "@plays":
        return str(plays) if plays else ""
    if not last:
        return ""
    try:
        return (
            datetime.fromisoformat(last.replace("Z", "+00:00"))
            .astimezone()
            .strftime("%Y-%m-%d %H:%M")
        )
    except ValueError:
        return last


def normalize(value: str) -> str:
    # A spelling-insensitive form of a name: 'A and his orchestra', 'A & his
    # Orchestra' and
    # 'A and His Orchestra' all give the same result.
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"\s*[&+]\s*|\band\b", " and ", text)
    text = re.sub(r"[^\w\s]|_", "", text)
    text = re.sub(r"^the\s+", "", " ".join(text.split()))
    return text


class TagDb:
    # The whole database held in memory; writes go through to SQLite.

    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.ids: dict[str, int] = {}
        self.tags: dict[str, dict[str, list[str]]] = {}
        by_id: dict[int, dict[str, list[str]]] = {}
        for fid, name in self.con.execute(
            "SELECT id, path FROM media_file ORDER BY path"
        ):
            self.ids[name] = fid
            by_id[fid] = self.tags[name] = {}
        for fid, key, _pos, value in self.con.execute(
            "SELECT file_id, name, pos, value FROM tag ORDER BY file_id,"
            " name, pos"
        ):
            by_id[fid].setdefault(key, []).append(value)
        self.has_duplicates = any(
            r[1] == "duplicate_of"
            for r in self.con.execute("PRAGMA table_info(media_file)")
        )
        self.dup_of: dict[str, str] = (
            {}
        )  # duplicate file -> the primary it duplicates
        self.copies: dict[str, list[str]] = {}  # primary -> its duplicates
        if self.has_duplicates:
            paths = {fid: name for name, fid in self.ids.items()}
            for name, primary in self.con.execute(
                "SELECT path, duplicate_of FROM media_file WHERE duplicate_of"
                " IS NOT NULL"
            ):
                self.dup_of[name] = paths[primary]
            self._index_copies()

        # library data kept beside the tags (not tags): rating (None = not
        # rated), play count, last played (UTC ISO)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(media_file)")}
        self.stats: dict[str, tuple[int | None, int, str | None]] = {}
        if {"rating", "play_count", "last_played"} <= cols:
            for name, rating, plays, last in self.con.execute(
                "SELECT path, rating, play_count, last_played FROM media_file"
            ):
                self.stats[name] = (rating, plays or 0, last)

        # playlists (schema version 4); a version 3 database gets the tables the
        # first time it is opened here
        with self.con:
            self.con.executescript(PLAYLIST_SCHEMA)
            have = {
                r[1] for r in self.con.execute("PRAGMA table_info(playlist)")
            }
            if (
                "rules" not in have
            ):  # dynamic playlists were added after the first playlist tables
                self.con.execute("ALTER TABLE playlist ADD COLUMN rules TEXT")
                self.con.execute(
                    "ALTER TABLE playlist ADD COLUMN played TEXT NOT NULL"
                    " DEFAULT '[]'"
                )
            if "kind" not in have:  # containers and sequences (version 5)
                self.con.execute(
                    "ALTER TABLE playlist ADD COLUMN kind TEXT NOT NULL DEFAULT"
                    " 'manual'"
                )
                self.con.execute(
                    "ALTER TABLE playlist ADD COLUMN limit_seconds INTEGER"
                )
                self.con.execute(
                    "ALTER TABLE playlist ADD COLUMN end_mode TEXT NOT NULL"
                    " DEFAULT 'fade'"
                )
                self.con.execute(
                    "ALTER TABLE playlist ADD COLUMN when_empty TEXT NOT NULL"
                    " DEFAULT 'repeat'"
                )
                self.con.execute(
                    "UPDATE playlist SET kind = 'dynamic' WHERE rules IS NOT"
                    " NULL"
                )
            self.con.execute(
                "UPDATE meta SET value = '4' WHERE key = 'schema_version' AND"
                " value = '3'"
            )
            self.con.execute(
                "UPDATE meta SET value = '5' WHERE key = 'schema_version' AND"
                " value = '4'"
            )
        if self.con.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone() == ("5",):
            self._separate_name_spaces()
            have = {
                r[1] for r in self.con.execute("PRAGMA table_info(media_file)")
            }
            for column in (
                "loudness",
                "peak",
                # added after version 4: measured loudness (LUFS) and true peak
                # (dBFS)
            ):
                if column not in have:
                    self.con.execute(
                        f"ALTER TABLE media_file ADD COLUMN {column} REAL"
                    )
        self._phone_columns()
        self.path_of: dict[int, str] = {
            fid: name for name, fid in self.ids.items()
        }
        self.load_loudness()

    def _phone_columns(self) -> None:
        # Version 7: what the phone sync needs.  media_file.stats_modified says
        # when the play count, last played time or
        # rating of a file was last written (by the desktop or the phone; the
        # newer one wins at a sync); playlist_item.removed_at
        # is set by the phone on a dynamic-playlist entry it has played (the
        # desktop removes it and refills the list).
        with self.con:
            if "stats_modified" not in {
                r[1] for r in self.con.execute("PRAGMA table_info(media_file)")
            }:
                self.con.execute(
                    "ALTER TABLE media_file ADD COLUMN stats_modified TEXT"
                )
            if "removed_at" not in {
                r[1]
                for r in self.con.execute("PRAGMA table_info(playlist_item)")
            }:
                self.con.execute(
                    "ALTER TABLE playlist_item ADD COLUMN removed_at TEXT"
                )
            self.con.execute(
                "UPDATE meta SET value = '7' WHERE key = 'schema_version' AND"
                " value = '6'"
            )

    def _separate_name_spaces(self) -> None:
        # Version 6: playlists, containers and sequences each have their own
        # names, so the table loses the UNIQUE on
        # name (SQLite cannot drop it: the table is rebuilt, keeping every id)
        # and gets PLAYLIST_NAME_INDEX instead.
        columns = (
            "id, name, created, rules, played, kind, limit_seconds, end_mode,"
            " when_empty"
        )
        self.con.commit()
        self.con.execute(
            "PRAGMA foreign_keys = OFF"
        )  # the tables that point at playlist(id) must survive the rebuild
        try:
            self.con.executescript(f"""BEGIN;
                CREATE TABLE playlist_new ({PLAYLIST_COLUMNS});
                INSERT INTO playlist_new ({columns})
                    SELECT {columns} FROM playlist;
                DROP TABLE playlist;
                ALTER TABLE playlist_new RENAME TO playlist;
                {PLAYLIST_NAME_INDEX}
                UPDATE meta SET value = '6' WHERE key = 'schema_version';
                COMMIT;""")
        except sqlite3.Error:
            self.con.execute("ROLLBACK")
            raise
        finally:
            self.con.execute("PRAGMA foreign_keys = ON")
        if self.con.execute("PRAGMA foreign_key_check").fetchall():
            raise sqlite3.IntegrityError(
                "the playlist tables do not match after the rebuild"
            )

    # -- loudness (measured by loudness.py): leveling gain for the player --

    def load_loudness(self) -> None:
        # loudness (LUFS) and peak (dBFS) of each measured file, and the
        # loudness of each album.
        self.loud: dict[str, tuple[float, float | None]] = {
            p: (l, k)
            for p, l, k in self.con.execute(
                "SELECT path, loudness, peak FROM media_file WHERE loudness IS"
                " NOT NULL"
            )
        }
        groups: dict[tuple[str, str], list[float]] = {}
        self.album_of: dict[str, tuple[str, str]] = {}
        for path in self.tags:
            album = next(
                (v for v in self.tags[path].get("ALBUM", []) if v.strip()), ""
            )
            self.album_of[path] = (os.path.dirname(path), album)
            if path in self.loud:
                groups.setdefault(self.album_of[path], []).append(
                    self.loud[path][0]
                )
        # an album's loudness is the power average of its tracks (equal weights:
        # durations are not stored)
        self.album_loudness = {
            k: 10 * math.log10(sum(10 ** (l / 10) for l in v) / len(v))
            for k, v in groups.items()
        }

    def set_loudness(self, path: str, loudness: float, peak: float) -> None:
        # The measured loudness (LUFS) and true peak (dBFS) of one file.
        with self.con:
            self.con.execute(
                "UPDATE media_file SET loudness = ?, peak = ? WHERE id = ?",
                (loudness, peak, self.ids[path]),
            )
        self.load_loudness()

    def gain_db(self, path: str, mode: str, target: float) -> float:
        # Gain in dB that brings the file (mode "track") or its album (mode
        # "album") to the target loudness (LUFS);
        # 0 when off or not measured.
        if mode == "off" or path not in self.loud:
            return 0.0
        level = (
            self.album_loudness.get(self.album_of.get(path), self.loud[path][0])
            if mode == "album"
            else self.loud[path][0]
        )
        return target - level

    # -- playlists: a playlist keeps its own order and may hold a file more than
    # once --

    # -- dynamic playlists: rules (see dynamic_playlists.py) select up to
    # dynamic_length() tracks; a track
    #    leaves the list when it is played (a play counts after 20 s) and a new
    #    one is added at the end --

    def dynamic_length(self) -> int:
        row = self.con.execute(
            "SELECT value FROM meta WHERE key = 'dynamic_length'"
        ).fetchone()
        try:
            return (
                max(1, int(row[0])) if row else dynamic_playlists.DEFAULT_LENGTH
            )
        except ValueError:
            return dynamic_playlists.DEFAULT_LENGTH

    def set_dynamic_length(self, length: int) -> None:
        with self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES"
                " ('dynamic_length', ?)",
                (str(max(1, int(length))),),
            )

    def scan_ignored(self) -> list[str]:
        # Files (relative to the music folder) that a scan does not list.
        row = self.con.execute(
            "SELECT value FROM meta WHERE key = 'scan_ignored'"
        ).fetchone()
        try:
            return sorted(json.loads(row[0])) if row else []
        except ValueError:
            return []

    def set_scan_ignored(self, paths: list[str]) -> None:
        with self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES"
                " ('scan_ignored', ?)",
                (json.dumps(sorted(set(paths))),),
            )

    def dynamic_ids(self) -> set[int]:
        return {
            r[0]
            for r in self.con.execute(
                "SELECT id FROM playlist WHERE rules IS NOT NULL"
            )
        }

    def get_rules(self, playlist_id: int) -> dict | None:
        # The rules of a dynamic playlist, None for a manual one.
        row = self.con.execute(
            "SELECT rules FROM playlist WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def create_dynamic(self, name: str, rules: dict) -> int:
        # A new dynamic playlist, filled at once; sqlite3.IntegrityError if the
        # name is taken.
        self._check_name(name)
        error = dynamic_playlists.validate(rules)
        if error:
            raise ValueError(error)
        with self.con:
            pid = self.con.execute(
                "INSERT INTO playlist (name, created, rules) VALUES (?, ?, ?)",
                (
                    name,
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    json.dumps(rules),
                ),
            ).lastrowid
        self.regenerate(pid)
        return pid

    def set_rules(self, playlist_id: int, rules: dict) -> None:
        # New rules for a dynamic playlist; it is regenerated.
        error = dynamic_playlists.validate(rules)
        if error:
            raise ValueError(error)
        with self.con:
            self.con.execute(
                "UPDATE playlist SET rules = ? WHERE id = ? AND rules IS NOT"
                " NULL",
                (json.dumps(rules), playlist_id),
            )
        self.regenerate(playlist_id)

    def regenerate(self, playlist_id: int) -> None:
        # Select the playlist's tracks afresh, forgetting which were played.
        rules = self.get_rules(playlist_id)
        if rules is None:
            return
        chosen = dynamic_playlists.matches(self, rules)[: self.dynamic_length()]
        with self.con:
            self.con.execute(
                "DELETE FROM playlist_item WHERE playlist_id = ?",
                (playlist_id,),
            )
            self.con.execute(
                "UPDATE playlist SET played = '[]' WHERE id = ?", (playlist_id,)
            )
            self.con.executemany(
                "INSERT INTO playlist_item (playlist_id, pos, file_id) VALUES"
                " (?, ?, ?)",
                [
                    (playlist_id, pos, self.ids[p])
                    for pos, p in enumerate(chosen)
                ],
            )

    def _dynamic_played(self, path: str) -> list[int]:
        # This file was played: every dynamic playlist forgets it for good
        # (until regenerated), and any other copy of the same song with it;
        # where they were on the list they are replaced by a new selection
        # added at the end.
        fid, changed = self.ids[path], []
        key = dynamic_playlists.song_key(self, path)
        for pid, rules_json, played_json in list(
            self.con.execute(
                "SELECT id, rules, played FROM playlist WHERE rules IS NOT NULL"
            )
        ):
            played = set(json.loads(played_json)) | {fid}
            copies = [
                entry
                for entry, p in self.playlist_entries(pid)
                if dynamic_playlists.song_key(self, p) == key
            ]
            with self.con:
                self.con.execute(
                    "UPDATE playlist SET played = ? WHERE id = ?",
                    (json.dumps(sorted(played)), pid),
                )
                if not copies:
                    continue
                self.con.executemany(
                    "DELETE FROM playlist_item WHERE id = ?",
                    [(entry,) for entry in copies],
                )
            self._refill(pid, json.loads(rules_json), played)
            changed.append(pid)
        return changed

    def top_up_dynamic(self) -> list[int]:
        # Drop the tracks that no longer fit the rules of every dynamic
        # playlist (a song was played from another copy, a rating changed, ...)
        # and fill the playlists that hold fewer tracks than the configured
        # length (the length was raised, entries were removed, ...) with new
        # tracks at the end.  Returns the playlists that changed.
        changed = []
        songs = dynamic_playlists.song_stats(self)
        for pid, rules_json, played_json in list(
            self.con.execute(
                "SELECT id, rules, played FROM playlist WHERE rules IS NOT NULL"
            )
        ):
            rules = json.loads(rules_json)
            before = self.playlist_entries(pid)
            fits = set(dynamic_playlists.matches(self, rules, songs=songs))
            stale = [entry for entry, p in before if p not in fits]
            if stale:
                with self.con:
                    self.con.executemany(
                        "DELETE FROM playlist_item WHERE id = ?",
                        [(entry,) for entry in stale],
                    )
                self.set_playlist_order(
                    pid, [e for e, _p in self.playlist_entries(pid)]
                )  # close the gaps
            count = len(before) - len(stale)
            if count < self.dynamic_length():
                self._refill(
                    pid, rules, set(json.loads(played_json)), songs
                )
            if stale or (
                self.con.execute(
                    "SELECT COUNT(*) FROM playlist_item WHERE playlist_id = ?",
                    (pid,),
                ).fetchone()[0]
                != count
            ):
                changed.append(pid)
        return changed

    def _refill(
        self, playlist_id: int, rules: dict, played: set[int], songs=None
    ) -> None:
        # Top the list up to the configured length with tracks not on it and not
        # played since it was generated.
        entries = [p for _e, p in self.playlist_entries(playlist_id)]
        missing = self.dynamic_length() - len(entries)
        exclude = set(entries) | {
            self.path_of[f] for f in played if f in self.path_of
        }
        new = dynamic_playlists.matches(self, rules, exclude, songs)[
            : max(missing, 0)
        ]
        with self.con:
            (end,) = self.con.execute(
                "SELECT COALESCE(MAX(pos) + 1, 0) FROM playlist_item WHERE"
                " playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            self.con.executemany(
                "INSERT INTO playlist_item (playlist_id, pos, file_id) VALUES"
                " (?, ?, ?)",
                [
                    (playlist_id, end + i, self.ids[p])
                    for i, p in enumerate(new)
                ],
            )
        self.set_playlist_order(
            playlist_id, [e for e, _p in self.playlist_entries(playlist_id)]
        )  # close the gaps

    # -- containers (timed segments of playlists) and sequences (containers one
    # after another); see containers.py --

    END_MODES = ("fade", "cut", "finish")
    WHEN_EMPTY = ("repeat", "next")

    def kinds(self) -> dict[int, str]:
        # playlist id -> "manual", "dynamic", "container" or "sequence".
        return {
            pid: (
                "dynamic"
                if rules is not None
                else kind if kind in ("container", "sequence") else "manual"
            )
            for pid, kind, rules in self.con.execute(
                "SELECT id, kind, rules FROM playlist"
            )
        }

    def container_info(self, playlist_id: int) -> dict | None:
        # limit_seconds (None = no limit), end_mode and when_empty of a
        # container; None if it is not one.
        row = self.con.execute(
            "SELECT kind, limit_seconds, end_mode, when_empty FROM playlist"
            " WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if not row or row[0] != "container":
            return None
        return {
            "limit_seconds": row[1],
            "end_mode": row[2],
            "when_empty": row[3],
        }

    def holds_dynamic(self, playlist_id: int) -> bool:
        # A dynamic playlist, or a container or sequence that holds one; such
        # a list plays in its own order, never shuffled.
        kind = self.kinds().get(playlist_id)
        if kind == "dynamic":
            return True
        return kind in ("container", "sequence") and any(
            self.holds_dynamic(m) for m in self.members(playlist_id)
        )

    def members(self, parent_id: int) -> list[int]:
        # The ids of a container's playlists or a sequence's containers, in
        # order.
        return [
            r[0]
            for r in self.con.execute(
                "SELECT child_id FROM playlist_member WHERE parent_id = ? ORDER"
                " BY pos",
                (parent_id,),
            )
        ]

    def used_in(self, playlist_id: int) -> list[tuple[int, str]]:
        # (id, name) of the containers or sequences that include this playlist
        # or container.
        return list(
            self.con.execute(
                "SELECT DISTINCT p.id, p.name FROM playlist_member m JOIN"
                " playlist p ON p.id = m.parent_id WHERE m.child_id = ? ORDER"
                " BY p.name COLLATE NOCASE",
                (playlist_id,),
            )
        )

    def _check_container(
        self, limit_seconds, end_mode, when_empty, playlist_ids
    ) -> None:
        kinds = self.kinds()
        if end_mode not in self.END_MODES or when_empty not in self.WHEN_EMPTY:
            raise ValueError("Unknown end mode or empty behaviour.")
        if limit_seconds is not None and int(limit_seconds) < 1:
            raise ValueError(
                "The time limit must be at least one second (or none)."
            )
        if not playlist_ids:
            raise ValueError("A container needs at least one playlist.")
        for pid in playlist_ids:
            if kinds.get(pid) not in ("manual", "dynamic"):
                raise ValueError(
                    "A container holds playlists, not other containers or"
                    " sequences."
                )
        if limit_seconds is None and any(
            kinds[pid] == "dynamic" for pid in playlist_ids
        ):
            raise ValueError(
                "A dynamic playlist never ends, so a container that holds one"
                " needs a time limit."
            )

    def _set_members(self, parent_id: int, child_ids: list[int]) -> None:
        self.con.execute(
            "DELETE FROM playlist_member WHERE parent_id = ?", (parent_id,)
        )
        self.con.executemany(
            "INSERT INTO playlist_member (parent_id, pos, child_id) VALUES (?,"
            " ?, ?)",
            [(parent_id, pos, c) for pos, c in enumerate(child_ids)],
        )

    def create_container(
        self,
        name: str,
        limit_seconds: int | None,
        end_mode: str,
        when_empty: str,
        playlist_ids: list[int],
    ) -> int:
        # A new container; sqlite3.IntegrityError if the name is taken,
        # ValueError if it is not valid.
        self._check_name(name)
        self._check_container(limit_seconds, end_mode, when_empty, playlist_ids)
        with self.con:
            pid = self.con.execute(
                "INSERT INTO playlist (name, created, kind, limit_seconds,"
                " end_mode, when_empty) VALUES (?, ?, 'container', ?, ?, ?)",
                (
                    name,
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    limit_seconds,
                    end_mode,
                    when_empty,
                ),
            ).lastrowid
            self._set_members(pid, playlist_ids)
        return pid

    def update_container(
        self,
        container_id: int,
        limit_seconds: int | None,
        end_mode: str,
        when_empty: str,
        playlist_ids: list[int],
    ) -> None:
        if self.container_info(container_id) is None:
            raise ValueError("Not a container.")
        self._check_container(limit_seconds, end_mode, when_empty, playlist_ids)
        with self.con:
            self.con.execute(
                "UPDATE playlist SET limit_seconds = ?, end_mode = ?,"
                " when_empty = ? WHERE id = ?",
                (limit_seconds, end_mode, when_empty, container_id),
            )
            self._set_members(container_id, playlist_ids)

    def create_sequence(self, name: str, container_ids: list[int]) -> int:
        # A new sequence of containers; sqlite3.IntegrityError if the name is
        # taken, ValueError if it is not valid.
        self._check_name(name)
        self._check_sequence(container_ids)
        with self.con:
            pid = self.con.execute(
                "INSERT INTO playlist (name, created, kind) VALUES (?, ?,"
                " 'sequence')",
                (name, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            ).lastrowid
            self._set_members(pid, container_ids)
        return pid

    def update_sequence(
        self, sequence_id: int, container_ids: list[int]
    ) -> None:
        if self.kinds().get(sequence_id) != "sequence":
            raise ValueError("Not a sequence.")
        self._check_sequence(container_ids)
        with self.con:
            self._set_members(sequence_id, container_ids)

    def _check_sequence(self, container_ids: list[int]) -> None:
        kinds = self.kinds()
        if not container_ids:
            raise ValueError("A sequence needs at least one container.")
        if any(kinds.get(c) != "container" for c in container_ids):
            raise ValueError("A sequence holds containers only.")

    def container_tracks(self, container_id: int) -> list[str]:
        # The paths of all tracks of a container's playlists, in order (a
        # dynamic playlist contributes its current list).
        paths: list[str] = []
        for pid in self.members(container_id):
            paths += [p for _e, p in self.playlist_entries(pid)]
        return paths

    def playlists(self) -> list[tuple[int, str, int]]:
        # (id, name, number of entries) of every playlist, by name.
        return list(
            self.con.execute(
                "SELECT p.id, p.name, COUNT(i.id) FROM playlist p LEFT JOIN"
                " playlist_item i ON i.playlist_id = p.id GROUP BY p.id ORDER"
                " BY p.name COLLATE NOCASE"
            )
        )

    @staticmethod
    def _check_name(name: str) -> None:
        if is_reserved_playlist_name(name):
            raise ValueError(
                f"The name \u201c{RESERVED_PLAYLIST}\u201d is reserved for the"
                " Samples tab and cannot be used."
            )

    def create_playlist(self, name: str, reserved: bool = False) -> int:
        # A new empty playlist; sqlite3.IntegrityError if the name is taken,
        # ValueError if it is the reserved name (reserved: the caller is the
        # Samples tab, which makes that playlist).
        if not reserved:
            self._check_name(name)
        with self.con:
            return self.con.execute(
                "INSERT INTO playlist (name, created) VALUES (?, ?)",
                (name, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            ).lastrowid

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        self._check_name(name)
        with self.con:
            self.con.execute(
                "UPDATE playlist SET name = ? WHERE id = ?", (name, playlist_id)
            )

    def delete_playlist(self, playlist_id: int) -> None:
        with self.con:
            self.con.execute(
                "DELETE FROM playlist WHERE id = ?", (playlist_id,)
            )

    def playlist_entries(self, playlist_id: int) -> list[tuple[int, str]]:
        # (entry id, file path) in playlist order.
        return [
            (item, self.path_of[fid])
            for item, fid in self.con.execute(
                "SELECT id, file_id FROM playlist_item WHERE playlist_id = ?"
                " ORDER BY pos",
                (playlist_id,),
            )
        ]

    def add_to_playlist(self, playlist_id: int, paths: list[str]) -> int:
        # Append these files; a duplicate is replaced by the primary it
        # duplicates.  Returns how many were added.
        if playlist_id in self.dynamic_ids():
            raise ValueError("A dynamic playlist selects its own tracks.")
        if self.kinds().get(playlist_id) in ("container", "sequence"):
            raise ValueError(
                "A container or sequence holds playlists, not tracks."
            )
        paths = [self.dup_of.get(p, p) for p in paths if p in self.ids]
        with self.con:
            (end,) = self.con.execute(
                "SELECT COALESCE(MAX(pos) + 1, 0) FROM playlist_item WHERE"
                " playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            self.con.executemany(
                "INSERT INTO playlist_item (playlist_id, pos, file_id) VALUES"
                " (?, ?, ?)",
                [
                    (playlist_id, end + i, self.ids[p])
                    for i, p in enumerate(paths)
                ],
            )
        return len(paths)

    def remove_from_playlist(
        self, playlist_id: int, entry_ids: list[int]
    ) -> None:
        with self.con:
            self.con.executemany(
                "DELETE FROM playlist_item WHERE playlist_id = ? AND id = ?",
                [(playlist_id, e) for e in entry_ids],
            )
        self.set_playlist_order(
            playlist_id, [e for e, _p in self.playlist_entries(playlist_id)]
        )  # close the gaps

    def set_playlist_order(
        self, playlist_id: int, entry_ids: list[int]
    ) -> None:
        # Put the playlist's entries in this order (entry_ids must be exactly
        # its entries).
        with self.con:
            self.con.executemany(
                "UPDATE playlist_item SET pos = ? WHERE playlist_id = ? AND"
                " id = ?",
                [(pos, playlist_id, e) for pos, e in enumerate(entry_ids)],
            )

    def record_play(self, path: str) -> None:
        # One more play of this file, now.  The count is incremented in SQL, so
        # a play recorded by
        # another program since this one loaded is not overwritten.  The track
        # stays on its dynamic
        # playlists until finish_play() is called (when it has ended).
        with self.con:
            self.con.execute(
                "UPDATE media_file SET play_count = play_count + 1, last_played"
                " = ?, stats_modified = ? WHERE id = ?",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    now_stamp(),
                    self.ids[path],
                ),
            )
            rating, plays, last = self.con.execute(
                "SELECT rating, play_count, last_played FROM media_file WHERE"
                " id = ?",
                (self.ids[path],),
            ).fetchone()
        self.stats[path] = (rating, plays, last)

    def finish_play(self, path: str) -> list[int]:
        # A counted play of this file has ended.  Returns the dynamic playlists
        # whose tracks changed because of it
        # (the track dropped off and a replacement was added).
        return self._dynamic_played(path)

    def set_ratings(self, paths: list[str], rating: int | None) -> None:
        # Rate these files 0 to 5, or None for not rated.
        if rating is not None and not (
            isinstance(rating, int) and 0 <= rating <= 5
        ):
            raise ValueError(f"rating must be 0 to 5 or None, not {rating!r}")
        with self.con:
            for path in paths:
                self.con.execute(
                    "UPDATE media_file SET rating = ?, stats_modified = ? WHERE"
                    " id = ?",
                    (rating, now_stamp(), self.ids[path]),
                )
        for path in paths:
            _old, plays, last = self.stats.get(path, (None, 0, None))
            self.stats[path] = (rating, plays, last)

    def _index_copies(self) -> None:
        self.copies = {}
        for name, primary in self.dup_of.items():
            self.copies.setdefault(primary, []).append(name)

    def set_duplicates(self, marks: dict[str, str | None]) -> None:
        # Set duplicate_of for each file: the primary's path, or None to clear
        # the mark.
        with self.con:
            for path, primary in marks.items():
                self.con.execute(
                    "UPDATE media_file SET duplicate_of = ? WHERE id = ?",
                    (self.ids[primary] if primary else None, self.ids[path]),
                )
                if primary:
                    self.dup_of[path] = primary
                else:
                    self.dup_of.pop(path, None)
        self._index_copies()

    # -- the file list: adding, removing and re-reading files (see
    # library_scan.py) --

    def backup(self, what: str) -> str:
        # Copy the database to tags.db.bak-before-<what> (a number is added if
        # that exists); returns the new path.
        target = f"{self.path}.bak-before-{what}"
        n = 1
        while os.path.exists(target):
            n += 1
            target = f"{self.path}.bak-before-{what}-{n}"
        out = sqlite3.connect(target)
        with out:
            self.con.backup(out)
        out.close()
        return target

    def journal(self, name: str, records: list[dict]) -> str:
        # Append one JSON line per record to <database
        # folder>/<name>-journal.jsonl; returns that path.
        path = os.path.join(
            os.path.dirname(os.path.realpath(self.path)),
            f"{name}-journal.jsonl",
        )
        at = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(path, "a", encoding="utf-8") as out:
            for record in records:
                out.write(
                    json.dumps({"at": at, **record}, ensure_ascii=False) + "\n"
                )
        return path

    def root(self) -> str:
        row = self.con.execute(
            "SELECT value FROM meta WHERE key = 'root'"
        ).fetchone()
        return row[0] if row else ""

    def _resort(self) -> None:
        # Keep the in-memory dictionaries in path order.
        items = sorted(self.tags.items())
        self.tags.clear()
        self.tags.update(items)
        self.path_of = {fid: name for name, fid in self.ids.items()}

    def add_files(
        self, entries: dict[str, tuple[dict, dict[str, list[str]]]]
    ) -> None:
        # Insert new files: {path: (entry from read_tags, the tags to store)}.
        # An unreadable file (entry has
        # "error") is stored without tags, as the build does.
        with self.con:
            for path, (entry, tags) in entries.items():
                error = (
                    str(entry.get("error", "unknown"))
                    if "error" in entry
                    else None
                )
                cur = self.con.execute(
                    "INSERT INTO media_file(path, size, mtime, read_error)"
                    " VALUES (?, ?, ?, ?)",
                    (path, entry.get("size"), entry.get("mtime"), error),
                )
                for key, values in tags.items():
                    self.con.executemany(
                        "INSERT INTO tag VALUES (?, ?, ?, ?)",
                        [
                            (cur.lastrowid, key, i, v)
                            for i, v in enumerate(values)
                        ],
                    )
                self.ids[path] = cur.lastrowid
                self.tags[path] = {k: list(v) for k, v in tags.items()}
                self.stats[path] = (None, 0, None)
        self._resort()

    def remove_files(self, paths: list[str]) -> None:
        # Delete files from the database (never from the disk), with their tags
        # and playlist entries.  Files that
        # were duplicates of a removed file become ordinary files again.
        # Dynamic playlists are topped up.
        with self.con:
            for path in paths:
                self.con.execute(
                    "DELETE FROM media_file WHERE id = ?", (self.ids[path],)
                )  # cascades to tag, playlist_item
        for path in paths:
            for copy in self.copies.pop(path, []):
                self.dup_of.pop(copy, None)
            self.dup_of.pop(path, None)
            del self.ids[path], self.tags[path], self.stats[path]
        self._index_copies()
        self._resort()
        for pid, rules_json, played_json in list(
            self.con.execute(
                "SELECT id, rules, played FROM playlist WHERE rules IS NOT NULL"
            )
        ):
            self._refill(
                pid, json.loads(rules_json), set(json.loads(played_json))
            )

    def delete_from_disk(self, paths: list[str]) -> tuple[list[str], list[str]]:
        # Delete the files (paths relative to the music folder) from the disk,
        # then the folders that became empty, up to but not including the music
        # folder.  Returns (files that could not be deleted, folders removed).
        root = os.path.realpath(self.root())
        failed: list[str] = []
        folders: set[str] = set()
        for path in paths:
            full = os.path.join(root, path)
            if not os.path.realpath(full).startswith(root + os.sep):
                failed.append(path)  # outside the music folder: never touched
                continue
            try:
                os.remove(full)
            except FileNotFoundError:
                pass  # already gone
            except OSError:
                failed.append(path)
                continue
            folders.add(os.path.dirname(full))
        removed: list[str] = []
        for folder in sorted(folders, key=len, reverse=True):
            while os.path.realpath(folder).startswith(root + os.sep):
                try:
                    os.rmdir(folder)  # fails unless the folder is empty
                except OSError:
                    break
                removed.append(os.path.relpath(folder, root))
                folder = os.path.dirname(folder)
        return failed, removed

    def reread_files(
        self, entries: dict[str, tuple[dict, dict[str, list[str]]]]
    ) -> None:
        # Replace the stored tags, size and time of files that changed on disk
        # (the database edits are lost).
        with self.con:
            for path, (entry, tags) in entries.items():
                fid = self.ids[path]
                error = (
                    str(entry.get("error", "unknown"))
                    if "error" in entry
                    else None
                )
                self.con.execute(
                    "UPDATE media_file SET size = ?, mtime = ?, read_error = ?"
                    " WHERE id = ?",
                    (entry.get("size"), entry.get("mtime"), error, fid),
                )
                self.con.execute("DELETE FROM tag WHERE file_id = ?", (fid,))
                for key, values in tags.items():
                    self.con.executemany(
                        "INSERT INTO tag VALUES (?, ?, ?, ?)",
                        [(fid, key, i, v) for i, v in enumerate(values)],
                    )
                self.tags[path] = {k: list(v) for k, v in tags.items()}

    def write(self, paths: list[str], changes_for) -> None:
        # Apply changes_for(path) -> {tag: values | None} to each file, in one
        # transaction.
        with self.con:
            for path in paths:
                fid, tags = self.ids[path], self.tags[path]
                for key, values in changes_for(path).items():
                    if key == "TRACKNUMBER" and values:
                        values = [
                            normalize_track_number(v) for v in values
                        ]  # track numbers are integers
                    self.con.execute(
                        "DELETE FROM tag WHERE file_id = ? AND name = ?",
                        (fid, key),
                    )
                    if values:
                        self.con.executemany(
                            "INSERT INTO tag VALUES (?, ?, ?, ?)",
                            [(fid, key, i, v) for i, v in enumerate(values)],
                        )
                        tags[key] = list(values)
                    else:
                        tags.pop(key, None)
