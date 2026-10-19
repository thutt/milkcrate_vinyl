#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The desktop music player.
#
# The library is every file in the database.  Double-click a track (or press
# Enter) to play it; playback
# continues down the list as currently sorted and filtered.  Playlists
# (right-hand tab; right-click tracks to add
# them) play in order or shuffled.  Select tracks to edit their tags in the
# panel on the right; Save writes the changes to the database (the media files
# are not touched).
#
# usage: player.py --database DB        (the database is required)
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys

from PyQt6.QtCore import (
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QFont,
    QKeySequence,
    QPalette,
    QStandardItemModel,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTableView,
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
)

from rating_delegate import RATING_ROLE, STAR_MARGIN, STAR_SLOT, RatingDelegate
from tagdb import MAX_CHOICES, SPLIT_TAGS, TagDb, stat_text
import containers
from playlists import PlaylistPanel, populate_add_menu
from tag_editor import TagEditor
from phone_sync_dialog import open_sync_dialog

COLUMNS = [
    ("Title", "TITLE", 280),
    ("Artist", "ARTIST", 200),
    ("Album", "ALBUM", 200),
    ("#", "TRACKNUMBER", 40),
    ("Genre", "GENRE", 120),
    ("Year", "DATE", 60),
    ("Rating", "@rating", 80),
    ("Plays", "@plays", 50),
    ("Last played", "@last", 120),
    ("Album artist", "ALBUMARTIST", 160),
    ("Composer", "COMPOSER", 160),
    ("Disc", "DISCNUMBER", 40),
    ("BPM", "BPM", 50),
    ("BPM reliability", "BPM_RELIABILITY", 90),
]
HIDDEN_BY_DEFAULT = {
    "ALBUMARTIST",
    "COMPOSER",
    "DISCNUMBER",
    "BPM",
    "BPM_RELIABILITY",
}  # available in the Columns dialog
NUMERIC_COLUMNS = {"TRACKNUMBER", "DISCNUMBER", "BPM", "BPM_RELIABILITY"}
RELIABILITY_WORDS = {
    "0": "low",
    "1": "medium",
    "2": "high",
}  # how BPM_RELIABILITY is shown
# layouts saved before BPM was added hold this many columns (a QHeaderView
# state)
LEGACY_COLUMN_COUNT = 12
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
MAX_CONSECUTIVE_ERRORS = 5
# a play is recorded once more than this much of a track has actually been heard
PLAY_COUNTS_AFTER_MS = 20_000
MAX_STEP_MS = 1500  # position jumps bigger than this are seeks, not listening
SEPARATOR = "; "


def first_int(text: str) -> int:
    m = re.match(r"\s*(\d+)", text)
    return int(m.group(1)) if m else 0


SEARCH_FIELDS = {"title", "artist", "album", "genre", "composer", "path"}
SEARCH_TOKEN = re.compile(r'(?:(\w+):)?(?:"([^"]*)"?|(\S+))')


def parse_search(text: str) -> list[tuple[str | None, re.Pattern]]:
    # Turn the search box text into (field, pattern) tests that must all match.
    #
    # A word matches where a word of the text starts with it ("dio" finds Dio
    # and Dion, not audio or studio);
    # a leading "=" asks for the whole word ("=dio" finds only Dio); "quoted
    # words" match as a phrase;
    # "artist:dio" limits the test to one field (title, artist, album, genre,
    # composer, path).
    tests = []
    for field, quoted, word in SEARCH_TOKEN.findall(text.lower()):
        word = quoted if quoted else word
        if (
            field and field not in SEARCH_FIELDS
        ):  # not a field name: search for the text as typed
            word, field = f"{field}:{word}", ""
        whole = word.startswith("=")
        word = word.lstrip("=").strip()
        if not word:
            continue
        pattern = (
            r"(?<![^\W_])" + re.escape(word) + (r"(?![^\W_])" if whole else "")
        )  # boundaries: not inside a word
        tests.append((field or None, re.compile(pattern)))
    return tests


class LibraryModel(QAbstractTableModel):
    # One row per file in the database; tag values come from TagDb's in-memory
    # copy.

    def __init__(self, db: TagDb, parent=None):
        super().__init__(parent)
        self.db = db
        self.paths: list[str] = list(db.tags)
        self.playing: str | None = None
        self.bold = QFont()
        self.bold.setBold(True)
        self.italic = QFont()
        self.italic.setItalic(True)
        self.bold_italic = QFont(self.bold)
        self.bold_italic.setItalic(True)
        self.dim = QBrush(
            QApplication.palette().color(QPalette.ColorRole.PlaceholderText)
        )
        self.haystack: dict[str, str] = (
            {}
        )  # all searchable text of a file, lower case
        self.fields: dict[str, dict[str, str]] = (
            {}
        )  # the same split by field, for "artist:dio"
        for path in self.paths:
            self._index(path)

    def _index(self, path: str) -> None:
        tags = self.db.tags[path]
        self.fields[path] = {
            "title": "\n".join(tags.get("TITLE", [])).lower(),
            "artist": (
                "\n".join(
                    tags.get("ARTIST", []) + tags.get("ALBUMARTIST", [])
                ).lower()
            ),
            "album": "\n".join(tags.get("ALBUM", [])).lower(),
            "genre": "\n".join(tags.get("GENRE", [])).lower(),
            "composer": "\n".join(tags.get("COMPOSER", [])).lower(),
            "path": path.lower(),
        }
        self.haystack[path] = "\n".join(self.fields[path].values())

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.paths)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(
        self, section, orientation, role=Qt.ItemDataRole.DisplayRole
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return COLUMNS[section][0]
        return None

    def text(self, path: str, column: int) -> str:
        key = COLUMNS[column][1]
        if key.startswith("@"):
            return stat_text(self.db.stats.get(path, (None, 0, None)), key)
        values = self.db.tags[path].get(key, [])
        if key == "TITLE" and not any(v.strip() for v in values):
            return os.path.splitext(os.path.basename(path))[0]
        if key == "BPM_RELIABILITY":
            return SEPARATOR.join(
                RELIABILITY_WORDS.get(v.strip(), v) for v in values
            )
        return SEPARATOR.join(values)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        path = self.paths[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            text = self.text(path, index.column())
            return (
                ("▶ " + text)
                if index.column() == 0 and path == self.playing
                else text
            )
        if role == Qt.ItemDataRole.FontRole:
            duplicate = path in self.db.dup_of
            if path == self.playing:
                return self.bold_italic if duplicate else self.bold
            return self.italic if duplicate else None
        if role == Qt.ItemDataRole.ForegroundRole and path in self.db.dup_of:
            return self.dim
        if role == SORT_ROLE:
            key = COLUMNS[index.column()][1]
            if key.startswith(
                "@"
            ):  # sort by the stored values, not the displayed text
                rating, plays, last = self.db.stats.get(path, (None, 0, None))
                return {
                    "@rating": -1 if rating is None else rating,
                    "@plays": plays,
                    "@last": last or "",
                }[key]
            if key == "BPM_RELIABILITY":
                return first_int(
                    SEPARATOR.join(self.db.tags[path].get(key, []))
                )
            text = self.text(path, index.column())
            return (
                first_int(text) if key in NUMERIC_COLUMNS else text.casefold()
            )
        if role == RATING_ROLE and COLUMNS[index.column()][1] == "@rating":
            rating = self.db.stats.get(path, (None, 0, None))[0]
            return -1 if rating is None else rating
        if role == Qt.ItemDataRole.ToolTipRole:
            if COLUMNS[index.column()][1] == "@rating":
                return (
                    "Click to rate (a first click on a not-rated track gives"
                    " zero stars); double-click = not rated"
                )
            return self.duplicate_note(path) + path
        return None

    def duplicate_note(self, path: str) -> str:
        if path in self.db.dup_of:
            return f"Duplicate of {self.db.dup_of[path]}\n"
        copies = len(self.db.copies.get(path, ()))
        return (
            f"Primary, {copies} cop{'ies' if copies != 1 else 'y'}\n"
            if copies
            else ""
        )

    def row_of(self, path: str) -> int:
        return self.paths.index(path)

    def refresh(self, paths: list[str]) -> None:
        # Tags of these files changed in the database.
        for path in paths:
            self._index(path)
            row = self.row_of(path)
            self.dataChanged.emit(
                self.index(row, 0), self.index(row, len(COLUMNS) - 1)
            )

    def set_playing(self, path: str | None) -> None:
        old, self.playing = self.playing, path
        for p in (old, path):
            if p is not None and p in self.db.tags:
                row = self.row_of(p)
                self.dataChanged.emit(self.index(row, 0), self.index(row, 0))


class LibraryProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.needles: list[tuple[str | None, re.Pattern]] = []
        self.show_duplicates = False
        self.setSortRole(SORT_ROLE)

    def set_show_duplicates(self, show: bool) -> None:
        self.show_duplicates = show
        self.invalidateFilter()

    def set_filter(self, text: str) -> None:
        self.needles = parse_search(text)
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        model: LibraryModel = self.sourceModel()
        if not self.show_duplicates and model.paths[row] in model.db.dup_of:
            return False
        if not self.needles:
            return True
        path = model.paths[row]
        return all(
            rx.search(
                model.haystack[path]
                if field is None
                else model.fields[path][field]
            )
            for field, rx in self.needles
        )

    def lessThan(self, left, right) -> bool:
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        if (
            a == b
        ):  # keep albums in track order when sorting by artist / album / etc.
            a, b = left.row(), right.row()
        return a < b


def fmt_time(ms: int) -> str:
    s = max(ms, 0) // 1000
    return f"{s // 60}:{s % 60:02d}"


class RatingBar(QWidget):
    # The rating of the playing track: five clickable stars, a circle button
    # and, for a track not rated, the word
    # "unrated".  Clicking star N rates N stars (the same star again: 0); the
    # circle makes the rating 0, and at 0 makes it
    # unrated; clicking "unrated" starts at 0 stars.  rate(rating) is emitted
    # with 0..5 or None.
    rate = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rating: int | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.unrated_button = QToolButton()
        self.unrated_button.setText("unrated")
        self.unrated_button.setAutoRaise(True)
        self.unrated_button.setToolTip("Not rated: click to start rating")
        self.unrated_button.clicked.connect(lambda: self.rate.emit(0))
        self.circle = QToolButton()
        self.circle.setText("\u25cb")
        self.circle.setAutoRaise(True)
        self.circle.setToolTip(
            "0 stars; click again at 0 stars to make it unrated"
        )
        self.circle.clicked.connect(
            lambda: self.rate.emit(None if self.rating == 0 else 0)
        )
        self.stars = []
        for n in range(1, 6):
            star = QToolButton()
            star.setAutoRaise(True)
            star.clicked.connect(
                lambda _=False, n=n: self.rate.emit(
                    0 if n == self.rating else n
                )
            )
            self.stars.append(star)
        layout.addWidget(self.unrated_button)
        layout.addWidget(self.circle)
        for star in self.stars:
            layout.addWidget(star)
        self.set_rating(None, False)

    def set_rating(self, rating: int | None, enabled: bool = True) -> None:
        self.rating = rating
        unrated = rating is None
        self.unrated_button.setVisible(unrated)
        self.circle.setVisible(not unrated)
        for n, star in enumerate(self.stars, 1):
            star.setVisible(not unrated)
            star.setText(
                "\u2605" if rating is not None and n <= rating else "\u2606"
            )
        self.setEnabled(enabled)


class ColumnsDialog(QDialog):
    # Choose which columns the table shows (tick) and in which order (drag, or
    # Move up / Move down).

    def __init__(
        self,
        entries: list[tuple[str, bool]],
        defaults: list[tuple[str, bool]],
        parent=None,
    ):
        # entries / defaults: (column title, shown) in display order.
        super().__init__(parent)
        self.setWindowTitle("Columns")
        self.defaults = defaults
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.up = QPushButton("Move up")
        self.down = QPushButton("Move down")
        self.reset = QPushButton("Reset")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.reset.clicked.connect(lambda: self._fill(self.defaults))
        side = QVBoxLayout()
        for b in (self.up, self.down, self.reset):
            side.addWidget(b)
        side.addStretch()
        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        body.addLayout(side)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tick the columns to show; drag to reorder."))
        layout.addLayout(body)
        layout.addWidget(buttons)
        self._fill(entries)
        self.resize(360, 380)

    def _fill(self, entries: list[tuple[str, bool]]) -> None:
        self.list.clear()
        for title, shown in entries:
            item = QListWidgetItem(title)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self.list.setCurrentRow(0)

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        if 0 <= row + step < self.list.count():
            item = self.list.takeItem(row)
            self.list.insertItem(row + step, item)
            self.list.setCurrentItem(item)

    def _accept(self) -> None:
        if not any(
            self.result_entries()[i][1] for i in range(self.list.count())
        ):
            QMessageBox.warning(
                self, "Columns", "At least one column must be shown."
            )
            return
        self.accept()

    def result_entries(self) -> list[tuple[str, bool]]:
        return [
            (
                self.list.item(r).text(),
                self.list.item(r).checkState() == Qt.CheckState.Checked,
            )
            for r in range(self.list.count())
        ]


class MainWindow(QMainWindow):
    def __init__(self, db: TagDb):
        super().__init__()
        self.resize(1300, 760)
        self.db = db
        # dynamic playlists that are short (a longer length, removed entries)
        # are filled
        self.db.top_up_dynamic()
        self.root = self._media_root()
        self.current: list[str] = []
        self.playing: str | None = None
        self.errors_in_a_row = 0
        self.heard_ms = 0  # time actually listened to the current track
        self.last_position: int | None = None
        self.play_recorded = False  # the current track's play has been counted
        self.unfinished = (
            None  # counted track still on its dynamic playlists until it ends
        )
        self.pending_skips: list[str] = (
            []
            # skipped before counting: leave the dynamic playlists once a later
            # track counts
        )
        self.skippable = (
            False  # a track was started and has not been stopped since
        )
        self.heard: set[str] = (
            set()
        )  # tracks already played in this shuffle round
        self.history: list[str] = (
            []
        )  # tracks played, most recent last (for Previous when shuffling)
        self.queue: list[str] | None = (
            None  # the playlist being played; None = play from the library view
        )
        self.queue_pos = 0  # index in the queue of the track playing
        self.queue_playlist: int | None = None
        self.schedule: containers.Schedule | None = (
            None  # a container or sequence being played
        )
        # "finish" mode: the limit has passed, move on when this track ends
        self.section_pending = False
        self.fade = 1.0  # fade-out factor at the end of a section
        self._quiet = False
        self._last_selection = QItemSelection()

        self.model = LibraryModel(db, self)
        self.proxy = LibraryProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, (_n, _k, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(
                i, width
            )  # (the rating column is sized below, to fit its stars)
        self.rating_column = next(
            i for i, c in enumerate(COLUMNS) if c[1] == "@rating"
        )
        self.table.setItemDelegateForColumn(
            self.rating_column, RatingDelegate(self._rate_cell, self.table)
        )
        self.table.setColumnWidth(
            self.rating_column, 2 * STAR_MARGIN + 5 * STAR_SLOT
        )
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda _pos: self.edit_columns()
        )
        for i, (_n, key, _w) in enumerate(COLUMNS):
            self.table.setColumnHidden(i, key in HIDDEN_BY_DEFAULT)
        # (a double-click on the rating column clears the rating; it must not
        # also start playback)
        self.table.doubleClicked.connect(
            lambda index: index.column() != self.rating_column
            and self.play_index(index)
        )
        self.table.selectionModel().selectionChanged.connect(
            self._selection_changed
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            'Search words (or artist:dio, album:"the wall", =dio for the whole'
            " word)..."
        )
        self.search.setClearButtonEnabled(True)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(
            lambda: self.proxy.set_filter(self.search.text())
            or self._update_status()
        )
        self.search.textChanged.connect(lambda _: self.search_timer.start())

        # -- playback --
        self.leveling, self.leveling_target = (
            "track",
            -18.0,
        )  # volume leveling: off / track / album, target in LUFS
        self.audio = QAudioOutput()
        self.audio.setVolume(0.7)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(lambda d: self.seek.setRange(0, d))
        self.player.errorOccurred.connect(self._player_error)

        self.now_playing = QLabel("Nothing playing")
        self.now_playing.setStyleSheet("font-weight: bold")
        self.rating_bar = (
            RatingBar()
        )  # the playing track's rating, set here as on the phone
        self.rating_bar.rate.connect(self._rate_playing)
        self.section_label = (
            QLabel()
            # the section of a container / sequence being played, and the time
            # left
        )
        self.section_label.hide()
        self.prev_button = QPushButton("⏮")
        self.play_button = QPushButton("▶")
        self.next_button = QPushButton("⏭")
        self.stop_button = QPushButton("⏹")
        self.shuffle_button = QPushButton("🔀")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.setToolTip(
            "Random order (Ctrl+R): no track repeats until all have played"
        )
        self.shuffle_button.toggled.connect(self._shuffle_toggled)
        for b in (
            self.prev_button,
            self.play_button,
            self.next_button,
            self.stop_button,
            self.shuffle_button,
        ):
            b.setFixedWidth(44)
        self.prev_button.clicked.connect(self.previous)
        self.play_button.clicked.connect(self.toggle)
        self.next_button.clicked.connect(self.next)
        self.stop_button.clicked.connect(self.stop)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek.sliderReleased.connect(
            lambda: self.player.setPosition(self.seek.value())
        )
        self.time_label = QLabel("0:00 / 0:00")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(110)
        self.volume.valueChanged.connect(lambda _v: self._apply_volume())

        controls = QHBoxLayout()
        for w in (
            self.prev_button,
            self.play_button,
            self.next_button,
            self.stop_button,
            self.shuffle_button,
        ):
            controls.addWidget(w)
        controls.addWidget(self.seek, 1)
        controls.addWidget(self.time_label)
        controls.addWidget(QLabel("Vol"))
        controls.addWidget(self.volume)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.table, 1)
        now_row = QHBoxLayout()
        now_row.addWidget(self.now_playing, 1)
        now_row.addWidget(self.rating_bar)
        left_layout.addLayout(now_row)
        left_layout.addWidget(self.section_label)
        left_layout.addLayout(controls)

        # -- tag editing --
        self.summary = QLabel("No tracks selected")
        self.editor = TagEditor()
        self.editor.changed.connect(self._editor_changed)
        self.editor.suggest = self._suggest
        self.editor.split_keys = SPLIT_TAGS
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        self.revert_button = QPushButton("Revert")
        self.revert_button.clicked.connect(self.editor.revert)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.save_button)
        tags_page = QWidget()
        right_layout = QVBoxLayout(tags_page)
        right_layout.addWidget(self.summary)
        right_layout.addWidget(self.editor, 1)
        right_layout.addLayout(buttons)
        self.playlist_panel = PlaylistPanel(db, can_play=True)
        self.playlist_panel.play_requested.connect(self.play_playlist)
        self.playlist_panel.changed.connect(self._playlist_changed)
        right = QTabWidget()
        right.addTab(tags_page, "Tags")
        right.addTab(self.playlist_panel, "Playlists")
        self.right_tabs = right

        self.splitter = QSplitter()
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self.splitter)
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)

        self._make_actions()
        self._restore_layout()
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(500)
        self._layout_timer.timeout.connect(self._save_layout)
        self.splitter.splitterMoved.connect(
            lambda *_: self._layout_timer.start()
        )
        self.table.horizontalHeader().sectionMoved.connect(
            lambda *_: self._layout_timer.start()
        )
        self.table.horizontalHeader().sectionResized.connect(
            lambda *_: self._layout_timer.start()
        )
        self._editor_changed()
        self._state_changed()
        self._update_status()

    # -- library -----------------------------------------------------------

    def _media_root(self) -> str:
        row = self.db.con.execute(
            "SELECT value FROM meta WHERE key = 'root'"
        ).fetchone()
        return (
            row[0]
            if row and row[0]
            else os.path.dirname(os.path.abspath(self.db.path))
        )

    def full_path(self, path: str) -> str:
        return os.path.join(self.root, path)

    def _suggest(self, key: str) -> list[str]:
        values = {
            v
            for tags in self.db.tags.values()
            for v in tags.get(key, [])
            if v.strip()
        }
        return (
            sorted(values, key=str.lower)
            if 0 < len(values) <= MAX_CHOICES
            else []
        )

    def _update_status(self) -> None:
        shown, total = self.proxy.rowCount(), self.model.rowCount()
        self.status_label.setText(
            f"{shown} of {total} tracks"
            if shown != total
            else f"{total} tracks"
        )

    # -- playback ----------------------------------------------------------

    def path_at(self, row: int) -> str:
        return self.model.paths[
            self.proxy.mapToSource(self.proxy.index(row, 0)).row()
        ]

    def play_index(self, index) -> None:
        self.errors_in_a_row = 0
        self._end_schedule()
        self.queue, self.queue_playlist = (
            None,
            None,
        )  # playing from the library view again
        self.play_row(index.row())

    def play_row(self, row: int, record: bool = True) -> None:
        # Play the track at this row of the (sorted, filtered) view.
        if 0 <= row < self.proxy.rowCount():
            self.play_path(self.path_at(row), record)

    def play_path(self, path: str, record: bool = True) -> None:
        self._finish_track()
        if (
            self.playing is not None
            and self.playing != path
            and self.skippable
            and not self.play_recorded
            and self.playing not in self.pending_skips
        ):
            self.pending_skips.append(
                self.playing
            )  # left before it counted: a skip, which takes effect later
        if path in self.pending_skips:
            self.pending_skips.remove(
                path
            )  # back to a skipped track: the skip is cancelled
        self.skippable = True
        self.playing = path
        if record:
            self.heard.add(path)
            self.history.append(path)
            del self.history[:-1000]
        self.heard_ms, self.last_position, self.play_recorded = 0, None, False
        self.model.set_playing(path)
        if self.queue is None:
            row = self._row_in_view(path)
            if row >= 0:
                self.table.scrollTo(self.proxy.index(row, 0))
        full = self.full_path(path)
        if not os.path.exists(full):
            self._skip(f"Missing file: {path}")
            return
        self.player.setSource(QUrl.fromLocalFile(full))
        self._apply_volume()
        self.player.play()
        tags = self.db.tags[path]
        title = self.model.text(path, 0)
        artist = SEPARATOR.join(tags.get("ARTIST", []))
        self.now_playing.setText(f"{title} — {artist}" if artist else title)
        self._update_rating_bar()
        self.statusBar().clearMessage()

    def _row_in_view(self, path: str) -> int:
        # Row of this track in the current view, or -1 (e.g. it was filtered
        # out).
        index = self.proxy.mapFromSource(
            self.model.index(self.model.row_of(path), 0)
        )
        return index.row() if index.isValid() else -1

    def _view_row(self) -> int:
        return -1 if self.playing is None else self._row_in_view(self.playing)

    def _sequence(self) -> list[str]:
        # The tracks Next / Previous / shuffle move through: the playlist being
        # played, else the library view.
        if self.queue is not None:
            return self.queue
        return [self.path_at(r) for r in range(self.proxy.rowCount())]

    def _position(self) -> int:
        # Index in the sequence of the track playing, -1 if none.
        if self.queue is not None:
            return self.queue_pos if self.playing is not None else -1
        return self._view_row()

    def _play_at(self, position: int) -> None:
        # Play the track at this index of the sequence.
        if self.queue is not None:
            self.queue_pos = position
        self.play_path(self._sequence()[position])

    def _shuffle_toggled(self, on: bool) -> None:
        self.shuffle_action.setChecked(on)
        self.shuffle_button.setChecked(on)
        self.heard = {self.playing} if self.playing else set()
        self.history = [self.playing] if self.playing else []

    def _random_position(self) -> int:
        # A random index of the sequence whose track has not played this round;
        # once all tracks (each counted
        # once, even if a playlist lists it twice) have played, a new round
        # starts.
        sequence = self._sequence()
        if not sequence:
            return -1
        unplayed = [i for i, p in enumerate(sequence) if p not in self.heard]
        if not unplayed:
            self.heard = {self.playing} if self.playing else set()
            unplayed = [
                i for i, p in enumerate(sequence) if p not in self.heard
            ] or list(range(len(sequence)))
        return random.choice(unplayed)

    def next(self) -> None:
        if self.schedule is not None and self._schedule_next():
            return
        if self.shuffle_button.isChecked():
            position = self._random_position()
            if position >= 0:
                self._play_at(position)
            else:
                self.skippable = False
                self.player.stop()
            return
        position = self._position()
        if position + 1 < len(self._sequence()):
            self._play_at(position + 1)
        else:
            self.skippable = False  # end of the list: stopped, not skipped
            self.player.stop()

    def previous(self) -> None:
        if self.player.position() > 3000:
            self.player.setPosition(0)
            return
        if self.shuffle_button.isChecked():
            sequence = self._sequence()
            while len(self.history) > 1:
                self.history.pop()  # the playing track
                if self.history[-1] in sequence:
                    path = self.history.pop()  # play_path records it again
                    self._play_at(sequence.index(path))
                    return
            return
        position = self._position()
        if position > 0:
            self._play_at(position - 1)

    # -- playlists ---------------------------------------------------------

    def play_playlist(self, playlist_id: int, start_row: int, shuffle) -> None:
        # Play a playlist from this entry (-1 = the first, or a random one when
        # shuffling).  shuffle: True / False
        # sets the shuffle button; None leaves it as it is.
        if self.db.kinds().get(playlist_id) in ("container", "sequence"):
            self.play_schedule(playlist_id, start_row, shuffle)
            return
        self._end_schedule()
        paths = [p for _e, p in self.db.playlist_entries(playlist_id)]
        if not paths:
            return
        if shuffle is not None:
            self.shuffle_button.setChecked(shuffle)
        self.queue, self.queue_playlist, self.queue_pos = paths, playlist_id, 0
        self.errors_in_a_row = 0
        self.heard, self.history = set(), []
        shuffling = self.shuffle_button.isChecked()
        self._play_at(
            start_row
            if start_row >= 0
            else random.randrange(len(paths)) if shuffling else 0
        )

    # -- containers and sequences: timed sections, one after another ----------

    def play_schedule(self, playlist_id: int, start_row: int, shuffle) -> None:
        # Play a container, or a sequence from this section (-1 = the first).
        # The clock counts the time actually
        # spent playing; at a section's limit the next section starts, and after
        # the last one play stops.
        sections = containers.sections_for(self.db, playlist_id)
        if not sections:
            self.statusBar().showMessage(
                "Nothing to play: it has no containers.", 6000
            )
            return
        if shuffle is not None:
            self.shuffle_button.setChecked(shuffle)
        self.schedule = containers.Schedule(sections)
        self.schedule.index = start_row if 0 <= start_row < len(sections) else 0
        self.errors_in_a_row = 0
        self._start_section()

    def _start_section(self) -> None:
        # Begin the current section: its tracks from the playlists, a fresh
        # clock, full volume.
        empty = 0
        while self.schedule is not None and self.schedule.section is not None:
            section = self.schedule.section
            tracks = self.db.container_tracks(section.container_id)
            if tracks:
                self.queue, self.queue_playlist, self.queue_pos = (
                    tracks,
                    None,
                    0,
                )
                self.heard, self.history = set(), []
                self.section_pending = False
                self.fade = 1.0
                self._update_section_label()
                shuffling = self.shuffle_button.isChecked()
                self._play_at(random.randrange(len(tracks)) if shuffling else 0)
                return
            empty += 1
            self.statusBar().showMessage(
                f"“{section.name}” has no tracks; skipped.", 6000
            )
            if (
                empty >= len(self.schedule.sections)
                or not self.schedule.advance()
            ):
                break
        self._schedule_done("Nothing to play.")

    def _advance_section(self) -> None:
        if self.schedule is not None and self.schedule.advance():
            self._start_section()
        else:
            self._schedule_done("Sequence complete.")

    def _schedule_done(self, message: str) -> None:
        self._end_schedule()
        self.skippable = False
        self.player.stop()
        self.statusBar().showMessage(message, 15000)

    def _end_schedule(self) -> None:
        self.schedule = None
        self.section_pending = False
        if self.fade != 1.0:
            self.fade = 1.0
            self._apply_volume()
        self.section_label.hide()

    def _schedule_next(self) -> bool:
        # Next track while a schedule plays: True if it moved on to the next
        # section or started the section's
        # playlists again, False to let the usual next-track logic go on.
        if (
            self.section_pending
        ):  # "finish": the limit has passed and this track is over
            self._advance_section()
            return True
        section = self.schedule.section
        sequence = self.queue or []
        if self.shuffle_button.isChecked():
            exhausted = all(p in self.heard for p in sequence)
        else:
            exhausted = self._position() + 1 >= len(sequence)
        if not exhausted:
            return False
        if section.when_empty == "next":
            self._advance_section()
            return True
        tracks = self.db.container_tracks(
            section.container_id
        )  # start again (dynamic playlists have been refilled)
        if not tracks:
            self._advance_section()
            return True
        self.queue, self.queue_pos, self.heard = tracks, 0, set()
        self._play_at(
            random.randrange(len(tracks))
            if self.shuffle_button.isChecked()
            else 0
        )
        return True

    def next_section(self) -> None:
        if self.schedule is not None:
            self._advance_section()

    def stop(self) -> None:
        self._end_schedule()
        self.skippable = False  # stopped, not skipped
        self.player.stop()
        self._finish_track()

    def _update_section_label(self) -> None:
        if self.schedule is None or self.schedule.section is None:
            self.section_label.hide()
            return
        self.section_label.setText(self.schedule.describe())
        self.section_label.show()

    def _count_schedule(self, ms: int) -> None:
        # ms of music were heard: run the section clock, fade out near the limit
        # and move on when it is reached.
        schedule = self.schedule
        if schedule is None or schedule.section is None:
            return
        schedule.add(ms)
        self._update_section_label()
        if schedule.limit_reached():
            if schedule.section.end_mode == "finish":
                self.section_pending = True
            else:
                self._advance_section()
            return
        fade = schedule.fade_factor()
        if fade != self.fade:
            self.fade = fade
            self._apply_volume()

    def _playlist_changed(self, playlist_id: int) -> None:
        # A playlist was edited (-1: created, renamed or deleted).  If it is the
        # one playing, follow the change.
        if self.queue is None or self.queue_playlist is None:
            return
        if playlist_id not in (-1, self.queue_playlist):
            return
        if all(
            pid != self.queue_playlist for pid, _n, _c in self.db.playlists()
        ):
            self.queue, self.queue_playlist = (
                None,
                None,
            )  # deleted: the track playing finishes, then the library view
            return
        entries = self.db.playlist_entries(self.queue_playlist)
        playing = (
            self.queue[self.queue_pos]
            if 0 <= self.queue_pos < len(self.queue)
            else None
        )
        self.queue = [p for _e, p in entries]
        if (
            playing
            in self.queue
            # the nearest entry for the track playing keeps Next going from the
            # right place
        ):
            self.queue_pos = min(
                (i for i, p in enumerate(self.queue) if p == playing),
                key=lambda i: abs(i - self.queue_pos),
            )
        else:
            self.queue_pos = (
                min(self.queue_pos, len(self.queue)) - 1 if self.queue else -1
            )

    def _table_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if index.isValid() and not self.table.selectionModel().isRowSelected(
            index.row()
        ):
            self.table.selectRow(index.row())
        paths = self._selected_files()
        menu = QMenu(self)
        populate_add_menu(menu, self.db, paths, self, self._added_to_playlist)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _added_to_playlist(self, playlist_id: int) -> None:
        self.playlist_panel.refresh(select=playlist_id)
        self._playlist_changed(playlist_id)
        self.statusBar().showMessage("Added to playlist", 4000)

    def toggle(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        else:
            self.errors_in_a_row = 0
            if self.queue:
                self._play_at(min(max(self.queue_pos, 0), len(self.queue) - 1))
                return
            rows = self.table.selectionModel().selectedRows()
            self.play_row(rows[0].row() if rows else 0)

    def _skip(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        self.errors_in_a_row += 1
        if self.errors_in_a_row < MAX_CONSECUTIVE_ERRORS:
            QTimer.singleShot(0, self.next)
        else:
            self.stop()

    def _media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next()
        elif status == QMediaPlayer.MediaStatus.BufferedMedia:
            self.errors_in_a_row = 0

    def _player_error(self, _error, message: str) -> None:
        self._skip(f"Cannot play {self.playing}: {message}")

    def _state_changed(self, *_) -> None:
        playing = (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        self.play_button.setText("⏸" if playing else "▶")
        if (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            self._finish_track()
            if self.playing is None:
                self.now_playing.setText("Nothing playing")
                self._update_rating_bar()

    def _count_listening(self, position: int) -> None:
        # Add up the time actually heard; once it passes the threshold, record
        # the play (once per start).
        playing = (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        step = 0
        if (
            playing
            and self.last_position is not None
            and 0 < position - self.last_position <= MAX_STEP_MS
        ):
            step = position - self.last_position
            self.heard_ms += step
        self.last_position = position if playing else None
        if step:
            self._count_schedule(step)
        if (
            self.heard_ms > PLAY_COUNTS_AFTER_MS
            and not self.play_recorded
            and self.playing is not None
        ):
            self.play_recorded = True
            try:
                self.db.record_play(self.playing)
            except sqlite3.Error as e:
                self.statusBar().showMessage(
                    f"Could not record the play: {e}", 8000
                )
                return
            self.unfinished = (
                self.playing
            )  # leaves the dynamic playlists when it ends or is left
            self.model.refresh([self.playing])
            self._commit_skips()

    def _commit_skips(self) -> None:
        # The listener has settled on a track (it counted as played): skipped
        # tracks leave the dynamic playlists.
        skipped, self.pending_skips = self.pending_skips, []
        changed: set[int] = set()
        for path in skipped:
            if path in self.db.ids:
                try:
                    changed.update(self.db.finish_play(path))
                except sqlite3.Error as e:
                    self.statusBar().showMessage(
                        f"Could not update the dynamic playlists: {e}", 8000
                    )
                    return
        if changed:
            self.playlist_panel.refresh()
            for pid in changed:
                self._playlist_changed(pid)

    def _finish_track(self) -> None:
        # The counted track has ended or been left: dynamic playlists drop it
        # and add a new one.
        path, self.unfinished = self.unfinished, None
        if path is None:
            return
        try:
            changed = self.db.finish_play(path)
        except sqlite3.Error as e:
            self.statusBar().showMessage(
                f"Could not update the dynamic playlists: {e}", 8000
            )
            return
        if changed:
            self.playlist_panel.refresh()
            for pid in changed:
                self._playlist_changed(pid)

    def _position_changed(self, position: int) -> None:
        self._count_listening(position)
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.time_label.setText(
            f"{fmt_time(position)} / {fmt_time(self.player.duration())}"
        )

    # -- tag editing -------------------------------------------------------

    def _selected_files(self) -> list[str]:
        rows = sorted(
            i.row() for i in self.table.selectionModel().selectedRows()
        )
        return [self.path_at(r) for r in rows]

    def _selection_changed(self, *_) -> None:
        if self._quiet:
            return
        if not self._confirm_discard():
            self._quiet = True
            self.table.selectionModel().select(
                self._last_selection,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self._quiet = False
            return
        self._last_selection = QItemSelection(
            self.table.selectionModel().selection()
        )
        self.current = self._selected_files()
        self.editor.set_files([self.db.tags[p] for p in self.current])
        n = len(self.current)
        self.summary.setText(
            "No tracks selected"
            if n == 0
            else f"Editing {n} track{'s' if n != 1 else ''}"
            + (f": {os.path.basename(self.current[0])}" if n == 1 else "")
        )

    def set_rating(self, rating) -> None:
        # Rate the selected tracks (0 to 5, None = not rated).  Stored at once,
        # whatever tag edits are pending.
        paths = list(self.current)
        if not paths:
            return
        try:
            self.db.set_ratings(paths, rating)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return
        self.model.refresh(paths)
        self._update_rating_bar()

    def _update_rating_bar(self) -> None:
        path = self.playing
        if path is None or path not in self.db.stats:
            self.rating_bar.set_rating(None, False)
        else:
            self.rating_bar.set_rating(self.db.stats[path][0])

    def _rate_playing(self, rating) -> None:
        # A star of the playing track was clicked (whatever is selected in the
        # table is left alone).
        saved, self.current = self.current, (
            [self.playing] if self.playing else []
        )
        try:
            self.set_rating(rating)
        finally:
            self.current = saved

    def _rate_cell(self, index, rating) -> None:
        # A rating cell was clicked: rate that track (the Rating menu and
        # Ctrl+0..5 rate the whole selection).
        saved, self.current = self.current, [self.path_at(index.row())]
        try:
            self.set_rating(rating)
        finally:
            self.current = saved

    def _editor_changed(self) -> None:
        dirty = bool(self.editor.pending_changes())
        self.save_button.setEnabled(dirty)
        self.save_action.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)
        self.setWindowTitle("Music Player" + (" *" if dirty else ""))

    def save(self) -> bool:
        changes = self.editor.pending_changes()
        if not changes:
            return True
        paths = list(self.current)
        try:
            self.db.write(paths, lambda _p: changes)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return False
        self.model.refresh(paths)
        self.proxy.invalidate()  # re-sort / re-filter on the new values
        self.editor.set_files([self.db.tags[p] for p in paths])
        self._update_status()
        return True

    def _confirm_discard(self) -> bool:
        if not self.editor.pending_changes():
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save the changes to the selected tracks?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    # -- window ------------------------------------------------------------

    def _make_actions(self) -> None:
        def add(menu, text, shortcut, slot):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            return action

        menu = self.menuBar().addMenu("&File")
        add(
            menu,
            "&Open Database...",
            QKeySequence.StandardKey.Open,
            self.open_db,
        )
        self.save_action = add(
            menu, "&Save Tags", QKeySequence.StandardKey.Save, self.save
        )
        add(menu, "S&ync with Phone...", None, self.sync_phone)
        add(menu, "&Quit", QKeySequence.StandardKey.Quit, self.close)
        menu = self.menuBar().addMenu("&View")
        self.duplicates_action = add(
            menu, "Show &duplicates", None, self._show_duplicates_toggled
        )
        self.duplicates_action.setCheckable(True)
        self.duplicates_action.setToolTip(
            "Duplicates are hidden unless this is on; they are dimmed and"
            " italic when shown"
        )
        add(menu, "&Columns...", None, self.edit_columns)
        levels = menu.addMenu("Volume &leveling")
        self.leveling_actions = {}
        for key, text in (
            ("off", "&Off"),
            ("track", "&Track (each song to the target loudness)"),
            ("album", "&Album (keep the loudness differences within an album)"),
        ):
            action = add(
                levels,
                text,
                None,
                lambda _=False, key=key: self._set_leveling(key),
            )
            action.setCheckable(True)
            action.setChecked(key == self.leveling)
            self.leveling_actions[key] = action
        add(levels, "Target &loudness...", None, self._set_target)
        menu = self.menuBar().addMenu("&Rating")
        add(menu, "Not rated", "Ctrl+Backspace", lambda: self.set_rating(None))
        for n in range(6):
            add(
                menu,
                f"{n} star{'s' if n != 1 else ''}  "
                + "\u2605" * n
                + "\u2606" * (5 - n),
                f"Ctrl+{n}",
                lambda _=False, n=n: self.set_rating(n),
            )
        menu = self.menuBar().addMenu("&Playback")
        add(menu, "&Play / Pause", "Ctrl+Space", self.toggle)
        add(menu, "&Next", "Ctrl+Right", self.next)
        add(menu, "P&revious", "Ctrl+Left", self.previous)
        add(menu, "&Stop", "Ctrl+.", self.stop)
        add(menu, "Next &section", "Ctrl+Shift+Right", self.next_section)
        self.shuffle_action = add(
            menu,
            "Random &order",
            "Ctrl+R",
            lambda on: self.shuffle_button.setChecked(on),
        )
        self.shuffle_action.setCheckable(True)
        add(
            menu,
            "&Find",
            "Ctrl+F",
            lambda: (self.search.setFocus(), self.search.selectAll()),
        )

    def _column_entries(self, default: bool = False) -> list[tuple[str, bool]]:
        # (title, shown) of every column, in display order (or in the default
        # order and visibility).
        header = self.table.horizontalHeader()
        if default:
            return [
                (title, key not in HIDDEN_BY_DEFAULT)
                for title, key, _w in COLUMNS
            ]
        order = sorted(range(len(COLUMNS)), key=header.visualIndex)
        return [
            (COLUMNS[i][0], not self.table.isColumnHidden(i)) for i in order
        ]

    def edit_columns(self) -> None:
        dialog = ColumnsDialog(
            self._column_entries(), self._column_entries(default=True), self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        header = self.table.horizontalHeader()
        by_title = {title: i for i, (title, _k, _w) in enumerate(COLUMNS)}
        for position, (title, shown) in enumerate(dialog.result_entries()):
            logical = by_title[title]
            header.moveSection(header.visualIndex(logical), position)
            self.table.setColumnHidden(logical, not shown)
        self._layout_timer.start()

    def _apply_volume(self) -> None:
        # The slider sets the volume; volume leveling scales it for the playing
        # track (the output cannot go above full).
        gain = (
            self.db.gain_db(self.playing, self.leveling, self.leveling_target)
            if self.playing
            else 0.0
        )
        self.audio.setVolume(
            min(1.0, self.volume.value() / 100 * 10 ** (gain / 20)) * self.fade
        )

    def _set_leveling(self, mode: str) -> None:
        self.leveling = mode
        for key, action in self.leveling_actions.items():
            action.setChecked(key == mode)
        self._apply_volume()

    def _set_target(self) -> None:
        value, ok = QInputDialog.getDouble(
            self,
            "Leveling target",
            "Target loudness (LUFS; louder tracks are turned down to it,\n"
            "quieter ones are turned up as far as the volume slider allows):",
            self.leveling_target,
            -40.0,
            -5.0,
            1,
        )
        if ok:
            self.leveling_target = value
            self._apply_volume()

    def _show_duplicates_toggled(self, on: bool) -> None:
        self.proxy.set_show_duplicates(on)
        self._update_status()

    def open_db(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open tag database",
            os.path.dirname(self.db.path),
            "SQLite (*.db *.sqlite);;All files (*)",
        )
        if not path:
            return
        self.stop()
        self.playing = None
        self.queue, self.queue_playlist = None, None
        self._quiet = True
        self.db.con.close()
        self.db = TagDb(path)
        self.root = self._media_root()
        self.model = LibraryModel(self.db, self)
        self.proxy.setSourceModel(self.model)
        self.playlist_panel.set_db(self.db)
        self.current = []
        self._last_selection = QItemSelection()
        self.editor.set_files([])
        self._quiet = False
        self._update_status()

    def sync_phone(self) -> None:
        if not self._confirm_discard():
            return
        open_sync_dialog(self, self.db, self._phone_synced)

    def _phone_synced(self, report) -> None:
        # The phone's plays and ratings are in the database: show them, follow
        # the dynamic playlists that were refilled.
        self.model.refresh(report.paths)
        self.playlist_panel.refresh()
        for pid in report.playlists_changed:
            self._playlist_changed(pid)

    def _column_layout(self) -> dict:
        # The table's columns as they are now: order, hidden ones, and the width
        # of each shown one (by column key).
        header = self.table.horizontalHeader()
        order = sorted(range(len(COLUMNS)), key=header.visualIndex)
        return {
            "order": [COLUMNS[i][1] for i in order],
            "hidden": [
                COLUMNS[i][1] for i in order if self.table.isColumnHidden(i)
            ],
            "widths": {
                COLUMNS[i][1]: header.sectionSize(i)
                for i in order
                if not self.table.isColumnHidden(i)
            },
        }

    def _saved_columns(self, settings: QSettings) -> dict:
        try:
            layout = json.loads(settings.value("column_layout", "") or "null")
            if isinstance(layout, dict):
                return layout
        except (TypeError, ValueError):
            pass
        state = settings.value(
            "columns"
            # a layout saved by an older version: decode it with a header of its
            # size
        )
        if (
            state is None
            or settings.value("column_count", type=int) != LEGACY_COLUMN_COUNT
        ):
            return {}
        old = QHeaderView(Qt.Orientation.Horizontal)
        old.setModel(QStandardItemModel(0, LEGACY_COLUMN_COUNT))
        if not old.restoreState(state):
            return {}
        order = sorted(range(LEGACY_COLUMN_COUNT), key=old.visualIndex)
        return {
            "order": [COLUMNS[i][1] for i in order],
            "hidden": [COLUMNS[i][1] for i in order if old.isSectionHidden(i)],
            "widths": {
                COLUMNS[i][1]: old.sectionSize(i)
                for i in order
                if not old.isSectionHidden(i)
            },
        }

    def _apply_columns(self, layout: dict) -> None:
        # Show the columns as saved; columns the saved layout does not know
        # (added since) go last, hidden or not as by default.
        header = self.table.horizontalHeader()
        keys = [c[1] for c in COLUMNS]
        known = [k for k in layout.get("order", []) if k in keys]
        for position, key in enumerate(
            known + [k for k in keys if k not in known]
        ):
            header.moveSection(header.visualIndex(keys.index(key)), position)
        for i, key in enumerate(keys):
            hidden = (
                key in layout.get("hidden", [])
                if key in known
                else key in HIDDEN_BY_DEFAULT
            )
            self.table.setColumnHidden(i, hidden)
            width = layout.get("widths", {}).get(key)
            if width and not hidden and key != "@rating":
                self.table.setColumnWidth(i, width)
        if known and not any(
            not self.table.isColumnHidden(i) for i in range(len(keys))
        ):
            self.table.setColumnHidden(
                0, False
            )  # (a saved layout must never leave nothing to see)

    def _restore_layout(self) -> None:
        settings = QSettings("music-player", "player")
        geometry, splitter = settings.value("geometry"), settings.value(
            "splitter"
        )
        if geometry is not None:
            self.restoreGeometry(geometry)
        if splitter is not None:
            self.splitter.restoreState(splitter)
        self.shuffle_button.setChecked(
            settings.value("shuffle", False, type=bool)
        )
        self.duplicates_action.setChecked(
            settings.value("show_duplicates", False, type=bool)
        )
        self.proxy.set_show_duplicates(self.duplicates_action.isChecked())
        self.leveling_target = settings.value(
            "leveling_target", self.leveling_target, type=float
        )
        mode = settings.value("leveling", "track")
        self._set_leveling(
            mode if mode in ("off", "track", "album") else "track"
        )  # on (track mode) unless the user chose otherwise
        self._apply_columns(self._saved_columns(settings))
        if settings.contains(
            "volume"
        ):  # (value() with a type gives 0 when the setting is missing)
            self.volume.setValue(settings.value("volume", 70, type=int))

    def _save_layout(self) -> None:
        settings = QSettings("music-player", "player")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("splitter", self.splitter.saveState())
        settings.setValue("column_layout", json.dumps(self._column_layout()))
        settings.remove("columns")  # the old, count-dependent format
        settings.remove("column_count")
        settings.setValue("volume", self.volume.value())
        settings.setValue("shuffle", self.shuffle_button.isChecked())
        settings.setValue("show_duplicates", self.duplicates_action.isChecked())
        settings.setValue("leveling", self.leveling)
        settings.setValue("leveling_target", self.leveling_target)
        settings.sync()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_timer.start()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._layout_timer.start()

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self._save_layout()
        self.player.stop()
        self._finish_track()
        self.db.con.close()
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Music player driven by the SQLite tag database (created by"
            " desktop/initialize_database.py)."
        )
    )
    parser.add_argument(
        "--database",
        "-d",
        required=True,
        metavar="DB",
        help="the library database to open",
    )
    args, qt_args = parser.parse_known_args()
    path = args.database
    if not os.path.exists(path):
        print(
            f"{path}: no such database (create it with"
            " scripts/initialize-database)",
            file=sys.stderr,
        )
        return 1
    app = QApplication([sys.argv[0]] + qt_args)
    window = MainWindow(TagDb(path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
