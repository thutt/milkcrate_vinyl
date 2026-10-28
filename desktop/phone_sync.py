# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Merges plays and ratings made on the phone into the desktop database.
#
# The phone plays from a copy of tags.db and writes two kinds of things to it:
#   * play count, last played time and rating of a track, with
#     media_file.stats_modified set to the time of the write
#   * a mark (playlist_item.removed_at) on each dynamic-playlist entry it has
#     played
# Everything else (tags, playlists, rules, containers) is edited on the desktop
# only.
#
# merge() takes the phone's copy and changes the desktop database (TagDb):
#   * for each track the stats with the newer stats_modified win (the desktop's
#     own plays and ratings set it too)
#   * each marked entry is handled as if the track had been played on the
#     desktop: it leaves every dynamic playlist and a
#     replacement is added at the end (TagDb._dynamic_played)
# The result is then pushed back to the phone, which has no marks left in it.
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from schema import is_reserved_playlist_name


@dataclass
class Report:
    phone_version: str = ""
    stats_from_phone: int = 0  # tracks whose stats were taken from the phone
    stats_kept: int = (
        0  # tracks the phone had changed but the desktop had written later
    )
    plays_from_phone: int = 0  # plays that the phone added to the taken counts
    marked: int = 0  # tracks the phone played from dynamic playlists
    playlists_changed: set = field(default_factory=set)
    problems: list = field(default_factory=list)
    paths: list = field(default_factory=list)  # tracks whose stats changed
    # (path, rating, plays, last played) the desktop had before, for each
    # track that took its stats from the phone
    changes: list = field(default_factory=list)

    def text(self) -> str:
        if self.problems:
            return "; ".join(self.problems)
        return (
            f"{self.stats_from_phone} track(s) took their play count / last"
            f" played / rating from the phone ({self.plays_from_phone} more"
            f" plays), {self.stats_kept} kept the newer desktop values;"
            f" {self.marked} played track(s) left dynamic playlists"
            f" ({len(self.playlists_changed)} playlist(s) refilled)"
        )


def merge(db, phone_path: str) -> Report:
    # Apply the phone's copy of the database to db (a TagDb).  Nothing is
    # written to the phone's copy.
    report = Report()
    phone = sqlite3.connect(f"file:{phone_path}?mode=ro", uri=True)
    try:
        if phone.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            report.problems.append(
                "the phone's database failed its integrity check; nothing was"
                " merged"
            )
            return report
        tables = {
            r[0]
            for r in phone.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"meta", "media_file"} <= tables:
            return report  # an empty file: nothing from the phone
        row = phone.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        report.phone_version = row[0] if row else ""
        have = {r[1] for r in phone.execute("PRAGMA table_info(media_file)")}
        if "stats_modified" not in have:
            # an older database: the phone cannot have written anything
            return report

        for path, plays, last, rating, modified in phone.execute(
            "SELECT path, play_count, last_played, rating, stats_modified FROM"
            " media_file WHERE stats_modified IS NOT NULL"
        ):
            if path not in db.ids:
                continue
            fid = db.ids[path]
            mine_plays, mine, mine_rating, mine_last = db.con.execute(
                "SELECT play_count, stats_modified, rating, last_played FROM"
                " media_file WHERE id = ?",
                (fid,),
            ).fetchone()
            if mine is not None and mine >= modified:
                report.stats_kept += (
                    mine > modified
                )  # equal = nothing new from the phone
                continue
            with db.con:
                db.con.execute(
                    "UPDATE media_file SET play_count = ?, last_played = ?,"
                    " rating = ?, stats_modified = ? WHERE id = ?",
                    (plays, last, rating, modified, fid),
                )
            db.stats[path] = (rating, plays or 0, last)
            report.stats_from_phone += 1
            report.paths.append(path)
            report.changes.append((path, mine_rating, mine_plays, mine_last))
            report.plays_from_phone += max(0, (plays or 0) - (mine_plays or 0))

        if "removed_at" in {
            r[1] for r in phone.execute("PRAGMA table_info(playlist_item)")
        }:
            played = [
                p
                for (p,) in phone.execute(
                    "SELECT m.path FROM playlist_item i JOIN media_file m ON"
                    " m.id = i.file_id WHERE i.removed_at IS NOT NULL GROUP BY"
                    " m.path ORDER BY MIN(i.removed_at)"
                )
            ]
            for path in played:
                if path in db.ids:
                    report.marked += 1
                    report.paths.append(path)
                    report.playlists_changed.update(db._dynamic_played(path))
    finally:
        phone.close()
    report.playlists_changed.update(
        db.top_up_dynamic()
    )  # the lists are full again after the sync
    return report


def strip_reserved_playlist(con: sqlite3.Connection) -> None:
    # The playlist of the Samples tab stays on the desktop: the phone gets the
    # snippets as songs (and as ringtones), not the playlist, and a copy of it
    # that the phone already has goes with the database that replaces its own.
    # Only a manual playlist of that name is removed.
    for pid, name in list(
        con.execute(
            "SELECT id, name FROM playlist WHERE rules IS NULL AND kind NOT IN"
            " ('container', 'sequence')"
        )
    ):
        if is_reserved_playlist_name(name):
            con.execute(
                "DELETE FROM playlist_item WHERE playlist_id = ?", (pid,)
            )
            con.execute(
                "DELETE FROM playlist_member WHERE parent_id = ? OR"
                " child_id = ?",
                (pid, pid),
            )
            con.execute("DELETE FROM playlist WHERE id = ?", (pid,))
    con.commit()


def merge_and_snapshot(
    db, pulled: str | None, tmp: str, backup: str | None = None
) -> tuple[str, Report]:
    # Merge the phone's database (None: it has none) into db, after saving a
    # copy of db as backup (when given).
    # Returns a consistent single-file copy of the result, to send to the phone,
    # and the report.
    if backup:
        dst = sqlite3.connect(backup)
        db.con.backup(dst)
        dst.close()
    report = merge(db, pulled) if pulled else Report()
    if not report.problems:
        db.con.commit()
    import os

    snapshot = os.path.join(tmp, "tags.db")
    out = sqlite3.connect(snapshot)
    db.con.backup(out)
    strip_reserved_playlist(out)
    out.execute("PRAGMA journal_mode = DELETE")
    out.close()
    return snapshot, report
