# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Containers and sequences: timed groups of playlists played in order.
#
# A container holds one or more playlists and a time limit.  A sequence is an
# ordered list of containers (the same container
# may appear more than once).  Playing a sequence plays its containers one after
# another and then stops; playing a container
# alone plays just that segment.
#
# Schedule is what the player drives: it keeps the clock of the current section
# (counted only while music is actually
# playing), says when the limit is reached, how far a fade-out has gone, and
# which section is next.  The player asks
# Schedule for the tracks of a section (db.container_tracks) and plays them in
# its usual way.
from __future__ import annotations

from dataclasses import dataclass

FADE_MS = 3000  # a section that ends with "fade" fades out over this long


@dataclass
class Section:
    container_id: int
    name: str
    limit_ms: int | None  # None = no limit
    end_mode: str  # "fade", "cut" or "finish"
    when_empty: str  # "repeat" or "next"


def sections_for(db, playlist_id: int) -> list[Section]:
    # The sections to play for a container (one section) or a sequence (one per
    # container, in order).
    kind = db.kinds().get(playlist_id)
    ids = (
        [playlist_id]
        if kind == "container"
        else db.members(playlist_id) if kind == "sequence" else []
    )
    names = {pid: name for pid, name, _c in db.playlists()}
    sections = []
    for cid in ids:
        info = db.container_info(cid)
        if info is None:
            continue
        limit = info["limit_seconds"]
        sections.append(
            Section(
                cid,
                names[cid],
                None if limit is None else int(limit) * 1000,
                info["end_mode"],
                info["when_empty"],
            )
        )
    return sections


def fmt_clock(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Schedule:
    def __init__(self, sections: list[Section]):
        self.sections = sections
        self.index = 0
        self.elapsed_ms = 0  # of the current section, while playing

    @property
    def section(self) -> Section | None:
        return (
            self.sections[self.index]
            if 0 <= self.index < len(self.sections)
            else None
        )

    def add(self, ms: int) -> None:
        self.elapsed_ms += ms

    def remaining_ms(self) -> int | None:
        s = self.section
        return (
            None
            if s is None or s.limit_ms is None
            else max(0, s.limit_ms - self.elapsed_ms)
        )

    def limit_reached(self) -> bool:
        r = self.remaining_ms()
        return r is not None and r <= 0

    def fade_factor(self) -> float:
        # 1.0 normally; falls to 0.0 over the last FADE_MS of a section that
        # ends with a fade.
        s, r = self.section, self.remaining_ms()
        if s is None or r is None or s.end_mode != "fade" or r >= FADE_MS:
            return 1.0
        return max(0.0, r / FADE_MS)

    def total_remaining_ms(self) -> int | None:
        # Time left in the whole schedule; None if a section has no limit.
        if self.section is None:
            return 0
        here = self.remaining_ms()
        later = [s.limit_ms for s in self.sections[self.index + 1 :]]
        if here is None or any(x is None for x in later):
            return None
        return here + sum(later)

    def advance(self) -> bool:
        # Go to the next section with a fresh clock; False when there is none
        # (the schedule is complete).
        self.index += 1
        self.elapsed_ms = 0
        return self.section is not None

    def describe(self) -> str:
        s = self.section
        if s is None:
            return ""
        r, total = self.remaining_ms(), self.total_remaining_ms()
        parts = [f"{s.name} ({self.index + 1}/{len(self.sections)})"]
        parts.append("no limit" if r is None else f"{fmt_clock(r)} left")
        if total is not None and len(self.sections) > 1:
            parts.append(f"{fmt_clock(total)} in all")
        return " — ".join(parts)
