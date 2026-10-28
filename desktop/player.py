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

import sys

# a program of its own: it leaves no .pyc files behind
sys.dont_write_bytecode = True

import argparse
import bisect
import faulthandler
import json
import os
import random
import re
import signal
import sqlite3
from types import SimpleNamespace

from PyQt6.QtCore import (
    QAbstractProxyModel,
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QSettings,
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
from analysis_actions import detect_bpm, measure_loudness
from tagdb import MAX_CHOICES, SPLIT_TAGS, TagDb, stat_text
import containers
from search import parse_search, search_fields
from playlists import PlaylistPanel, populate_add_menu
from tag_editor import TagEditor
from bulk_values import change_value
from phone_review import review_phone_changes
from phone_sync_dialog import open_sync_dialog
from range_seek import RangeSeek, clock
import snippet

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
SAMPLES_TAB = 3
# the song list a tab shows; Library and Playlists use the Library's (0)
TAB_LIST = {2: 1, SAMPLES_TAB: 2}
# a play is recorded once more than this much of a track has actually been heard
PLAY_COUNTS_AFTER_MS = 20_000
MAX_STEP_MS = 1500  # position jumps bigger than this are seeks, not listening
SEPARATOR = "; "


def first_int(text: str) -> int:
    m = re.match(r"\s*(\d+)", text)
    return int(m.group(1)) if m else 0


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
        self.fields[path] = search_fields(path, tags)
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

    def add_path(self, path: str) -> None:
        # A file was added to the database (kept in path order, as there).
        row = bisect.bisect_left(self.paths, path)
        self.beginInsertRows(QModelIndex(), row, row)
        self.paths.insert(row, path)
        self._index(path)
        self.endInsertRows()

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


class LibraryProxy(QAbstractProxyModel):
    # The tracks that match the search, in the order of the sorted column.
    #
    # Qt's own sorting proxy asks the model for every value each time it
    # compares two rows, calling into Python hundreds of thousands of times;
    # sorting and filtering all the tracks took most of a second.  Here the
    # values are read once and sorted in Python.  A change in the tags does not
    # move a row until invalidate() (the player says when it can matter).

    def __init__(self, parent=None):
        super().__init__(parent)
        self.needles: list[tuple[str | None, re.Pattern]] = []
        self.show_duplicates = False
        self._rows: list[int] = []  # source rows that are shown, in order
        self._place: dict[int, int] = {}  # source row -> row here
        self._column = -1
        self._order = Qt.SortOrder.AscendingOrder

    def setSourceModel(self, model) -> None:
        self.beginResetModel()
        old = self.sourceModel()
        if old is not None:
            old.dataChanged.disconnect(self._source_changed)
            old.modelReset.disconnect(self._source_reset)
            old.rowsInserted.disconnect(self._source_inserted)
        super().setSourceModel(model)
        model.dataChanged.connect(self._source_changed)
        model.modelReset.connect(self._source_reset)
        model.rowsInserted.connect(self._source_inserted)
        self._arrange()
        self.endResetModel()

    def _source_reset(self) -> None:
        self.beginResetModel()
        self._arrange()
        self.endResetModel()

    def _source_inserted(self, _parent, first, last) -> None:
        # Rows were added to the source: the rows after them move down, and
        # the selection stays where it was.
        count = last - first + 1
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()
        kept = []
        for i in old:
            row = self._rows[i.row()]
            kept.append((row + count if row >= first else row, i.column()))
        self._arrange()
        self.changePersistentIndexList(
            old,
            [
                self.index(self._place[r], c) if r in self._place
                else QModelIndex()
                for r, c in kept
            ],
        )
        self.layoutChanged.emit()

    def _source_changed(self, top, bottom, roles=()) -> None:
        for row in range(top.row(), bottom.row() + 1):
            place = self._place.get(row)
            if place is not None:
                self.dataChanged.emit(
                    self.index(place, top.column()),
                    self.index(place, bottom.column()),
                    roles,
                )

    def _arrange(self) -> None:
        model: LibraryModel = self.sourceModel()
        if model is None:
            return
        rows = [
            r
            for r in range(model.rowCount())
            if self.filterAcceptsRow(r)
        ]
        if self._column >= 0:
            values = {
                r: model.data(model.index(r, self._column), SORT_ROLE)
                for r in rows
            }
            # ties keep the tracks in the order of the library (albums in
            # track order)
            rows.sort(
                key=lambda r: (values[r], r),
                reverse=self._order == Qt.SortOrder.DescendingOrder,
            )
        self._rows = rows
        self._place = {r: i for i, r in enumerate(rows)}

    def invalidate(self) -> None:
        # Filter and sort again, keeping the selection.
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()
        kept = [(self._rows[i.row()], i.column()) for i in old]
        self._arrange()
        self.changePersistentIndexList(
            old,
            [
                self.index(self._place[r], c) if r in self._place
                else QModelIndex()
                for r, c in kept
            ],
        )
        self.layoutChanged.emit()

    invalidateFilter = invalidate

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        self._column, self._order = column, order
        self.invalidate()

    def sortColumn(self) -> int:
        return self._column

    def set_show_duplicates(self, show: bool) -> None:
        self.show_duplicates = show
        self.invalidate()

    def set_filter(self, text: str) -> None:
        self.needles = parse_search(text)
        self.invalidate()

    def filterAcceptsRow(self, row) -> bool:
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

    # -- the model interface ----------------------------------------------

    def index(self, row, column, parent=QModelIndex()):
        if 0 <= row < len(self._rows) and not parent.isValid():
            return self.createIndex(row, column)
        return QModelIndex()

    def parent(self, child=QModelIndex()):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        model = self.sourceModel()
        return 0 if parent.isValid() or model is None else model.columnCount()

    _FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def flags(self, index):
        # The default asks the source for each cell, through mapToSource, in
        # Python; the view asks for many thousands when much is selected.
        return self._FLAGS if index.isValid() else Qt.ItemFlag.NoItemFlags

    def mapToSource(self, index):
        if not index.isValid():
            return QModelIndex()
        return self.sourceModel().index(self._rows[index.row()], index.column())

    def mapFromSource(self, index):
        place = self._place.get(index.row()) if index.isValid() else None
        return QModelIndex() if place is None else self.index(
            place, index.column()
        )


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


class TransportBar(QWidget):
    # What is playing and the controls: one for each tab, so that each tab
    # plays on its own (see MainWindow._activate).

    def __init__(self, samples: bool = False, parent=None):
        super().__init__(parent)
        self.now_playing = QLabel("Nothing playing")
        self.now_playing.setStyleSheet("font-weight: bold")
        self.rating_bar = (
            RatingBar()
        )  # the playing track's rating, set here as on the phone
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
        for b in (
            self.prev_button,
            self.play_button,
            self.next_button,
            self.stop_button,
            self.shuffle_button,
        ):
            b.setFixedWidth(44)
        # (the Samples tab's bar has two markers on it, for the snippet)
        self.seek = (
            RangeSeek() if samples else QSlider(Qt.Orientation.Horizontal)
        )
        self.time_label = QLabel("0:00 / 0:00")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(110)

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
        now_row = QHBoxLayout()
        now_row.addWidget(self.now_playing, 1)
        now_row.addWidget(self.rating_bar)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(now_row)
        layout.addWidget(self.section_label)
        layout.addLayout(controls)
        if samples:
            self.region_label = QLabel()
            self.save_button = QPushButton("✂  Save Snippet")
            self.save_button.setToolTip(
                "Save the part of the track between the two arrows as a file"
                " in the folder Track Samples, and add it to the playlist"
                " Track Samples"
            )
            self.save_button.setEnabled(False)
            # (the bar keeps the keyboard focus, so that the arrow keys move
            # the marker that was grabbed last)
            for b in (
                self.prev_button,
                self.play_button,
                self.next_button,
                self.stop_button,
                self.shuffle_button,
                self.save_button,
            ):
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row = QHBoxLayout()
            row.addWidget(self.region_label, 1)
            row.addWidget(self.save_button)
            layout.addLayout(row)


# The state of what a tab is playing, kept while another tab plays.
DECK_FIELDS = {
    "playing": None,
    "queue": None,
    "queue_pos": 0,
    "queue_playlist": None,
    "schedule": None,
    "heard": set,
    "history": list,
    "heard_ms": 0,
    "last_position": None,
    "play_recorded": False,
    "skippable": False,
    "section_pending": False,
    "fade": 1.0,
    "position": 0,
}


class MainWindow(QMainWindow):
    # The widgets of what is playing belong to the tab that plays: these
    # names are those of the active tab's bar.
    now_playing = property(lambda self: self.bars[self.active].now_playing)
    rating_bar = property(lambda self: self.bars[self.active].rating_bar)
    section_label = property(lambda self: self.bars[self.active].section_label)
    prev_button = property(lambda self: self.bars[self.active].prev_button)
    play_button = property(lambda self: self.bars[self.active].play_button)
    next_button = property(lambda self: self.bars[self.active].next_button)
    stop_button = property(lambda self: self.bars[self.active].stop_button)
    shuffle_button = property(
        lambda self: self.bars[self.active].shuffle_button
    )
    seek = property(lambda self: self.bars[self.active].seek)
    time_label = property(lambda self: self.bars[self.active].time_label)
    volume = property(lambda self: self.bars[self.active].volume)
    # The song list of the tab that is shown, and the one the playing tab
    # plays from (the Tags tab has its own list; the other tabs use the
    # Library's).
    lst = property(
        lambda self: self.lists[
            TAB_LIST.get(self.tabs.currentIndex(), 0)
            if getattr(self, "tabs", None)
            else 0
        ]
    )
    play_lst = property(lambda self: self.lists[TAB_LIST.get(self.active, 0)])
    table = property(lambda self: self.lst.table)
    proxy = property(lambda self: self.lst.proxy)
    search = property(lambda self: self.lst.search)

    def _make_song_list(self) -> SimpleNamespace:
        lst = SimpleNamespace(last_selection=QItemSelection())
        lst.proxy = LibraryProxy(self)
        lst.proxy.setSourceModel(self.model)
        table = lst.table = QTableView()
        table.setModel(lst.proxy)
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(22)
        table.horizontalHeader().setStretchLastSection(True)
        for i, (_n, _k, width) in enumerate(COLUMNS):
            table.setColumnWidth(
                i, width
            )  # (the rating column is sized below, to fit its stars)
        table.setItemDelegateForColumn(
            self.rating_column,
            RatingDelegate(
                lambda index, rating: self._rate_cell(index, rating, lst),
                table,
            ),
        )
        table.setColumnWidth(
            self.rating_column, 2 * STAR_MARGIN + 5 * STAR_SLOT
        )
        header = table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda _pos: self.edit_columns()
        )
        for i, (_n, key, _w) in enumerate(COLUMNS):
            table.setColumnHidden(i, key in HIDDEN_BY_DEFAULT)
        # (a double-click on the rating column clears the rating; it must not
        # also start playback)
        table.doubleClicked.connect(
            lambda index: index.column() != self.rating_column
            and self.play_index(index, lst)
        )
        table.selectionModel().selectionChanged.connect(
            lambda *_: self._selection_changed(lst)
        )
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos: self._table_menu(lst, pos)
        )

        search = lst.search = QLineEdit()
        search.setPlaceholderText(
            'Search words (or artist:dio, album:"the wall", =dio for the whole'
            " word)..."
        )
        search.setClearButtonEnabled(True)
        timer = lst.search_timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(250)
        timer.timeout.connect(
            lambda: lst.proxy.set_filter(search.text()) or self._update_status()
        )
        search.textChanged.connect(lambda _: timer.start())
        lst.pane = QWidget()
        layout = QVBoxLayout(lst.pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(search)
        layout.addWidget(table, 1)
        return lst

    def tidy_playlists(self) -> None:
        # Dynamic playlists that are short (a longer length, removed entries)
        # are filled; done after the window is shown.
        if self.db.top_up_dynamic():
            self.playlist_panel.refresh()

    def _make_sound(self) -> tuple:
        # The audio output takes up to 1.8 s to make (Qt looks up the sound
        # devices), so it is made after the window is shown, or at the first
        # play.
        if self._sound is None:
            audio = QAudioOutput()
            audio.setVolume(0.7)
            player = QMediaPlayer()
            player.setAudioOutput(audio)
            player.mediaStatusChanged.connect(self._media_status)
            player.playbackStateChanged.connect(self._state_changed)
            player.positionChanged.connect(self._position_changed)
            player.durationChanged.connect(lambda d: self.seek.setRange(0, d))
            player.errorOccurred.connect(self._player_error)
            self._sound = (audio, player)
            self._volume_applied = 0.7
            self._apply_volume()
        return self._sound

    @property
    def audio(self) -> QAudioOutput:
        return self._make_sound()[0]

    @property
    def player(self) -> QMediaPlayer:
        return self._make_sound()[1]

    def __init__(self, db: TagDb):
        super().__init__()
        self.resize(1300, 760)
        self.db = db
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
        self.shuffle_wanted = False  # the choice; a dynamic playlist overrides
        self._shuffle_locking = False
        self.schedule: containers.Schedule | None = (
            None  # a container or sequence being played
        )
        # "finish" mode: the limit has passed, move on when this track ends
        self.section_pending = False
        self.fade = 1.0  # fade-out factor at the end of a section
        self._quiet = False

        self.model = LibraryModel(db, self)
        self.rating_column = next(
            i for i, c in enumerate(COLUMNS) if c[1] == "@rating"
        )
        # Three song lists, entirely separate: the Library tab's, the Tags
        # tab's and the Samples tab's, each with its own search, sort, columns
        # and selection.
        self.lists = [self._make_song_list() for _ in range(3)]

        # -- playback --
        self.leveling, self.leveling_target = (
            "track",
            -18.0,
        )  # volume leveling: off / track / album, target in LUFS
        self._sound: tuple | None = None  # (audio output, media player)
        self._volume_applied: float | None = None  # what the output has now
        self._region_stopping = False
        # Some files never start (see _check_stalled_start).
        self.stalled_ms, self.start_kicks = 0, 0
        self.stall_timer = QTimer(self)
        self.stall_timer.setInterval(250)
        self.stall_timer.timeout.connect(self._check_stalled_start)
        self.stall_timer.start()


        self.bars = [
            TransportBar(),
            TransportBar(),
            TransportBar(),
            TransportBar(samples=True),
        ]
        self.active = 0  # the tab whose bar plays
        self.decks = [self._new_deck() for _ in self.bars]
        self._pending_seek: int | None = None
        self._switching = False
        for i, bar in enumerate(self.bars):
            bar.rating_bar.rate.connect(
                lambda r, i=i: (self._activate(i), self._rate_playing(r))
            )
            bar.shuffle_button.toggled.connect(self._shuffle_toggled)
            bar.prev_button.clicked.connect(
                lambda _c=False, i=i: self._bar_action(i, self.previous)
            )
            bar.play_button.clicked.connect(
                lambda _c=False, i=i: self._bar_action(i, self.toggle)
            )
            bar.next_button.clicked.connect(
                lambda _c=False, i=i: self._bar_action(i, self.next)
            )
            bar.stop_button.clicked.connect(
                lambda _c=False, i=i: self._bar_action(i, self.stop)
            )
            bar.seek.sliderMoved.connect(
                lambda p, i=i: self.active == i and self.player.setPosition(p)
            )
            bar.seek.sliderReleased.connect(
                lambda i=i: self.active == i
                and self.player.setPosition(self.bars[i].seek.value())
            )
            bar.volume.valueChanged.connect(
                lambda v, i=i: self._volume_moved(i, v)
            )
        samples = self.bars[SAMPLES_TAB]
        samples.seek.regionChanged.connect(
            lambda *_: self._update_samples_bar()
        )
        samples.seek.regionReleased.connect(self._region_released)
        samples.save_button.clicked.connect(self.save_snippet)

        # the library: the search box and the song list
        library_page = self.lists[0].pane

        # -- tag editing --
        self.summary = QLabel("No tracks selected")
        self.editor = TagEditor()
        self.editor.changed.connect(self._editor_changed)
        self.editor.suggest = self._suggest
        self.editor.bulk_requested.connect(self._bulk_value)
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
        # One task at a time: Library, Playlists or Tags (Ctrl+1, 2, 3).
        self.tabs = QTabWidget()
        self.tags_split = QSplitter()
        self.tags_split.addWidget(self.lists[1].pane)
        self.tags_split.addWidget(tags_page)
        self.tags_split.setStretchFactor(0, 3)
        self.tags_split.setStretchFactor(1, 2)
        # Each tab has what is playing and the controls of its own, below its
        # content, and plays on its own.
        for page, name, bar in zip(
            (
                library_page,
                self.playlist_panel,
                self.tags_split,
                self.lists[2].pane,
            ),
            ("&Library", "&Playlists", "&Tags", "&Samples"),
            self.bars,
        ):
            holder = QWidget()
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.addWidget(page, 1)
            holder_layout.addWidget(bar)
            self.tabs.addTab(holder, name)
        self.setCentralWidget(self.tabs)
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)

        self._make_actions()
        self._restore_layout()
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(500)
        self._layout_timer.timeout.connect(self._save_layout)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(self.tabs.currentIndex())
        for lst in self.lists:
            lst.table.horizontalHeader().sectionMoved.connect(
                lambda *_: self._layout_timer.start()
            )
            lst.table.horizontalHeader().sectionResized.connect(
                lambda *_: self._layout_timer.start()
            )
        self._editor_changed()
        self._state_changed()
        self._update_status()

    # -- the tabs play one at a time ----------------------------------------

    @staticmethod
    def _new_deck() -> dict:
        return {
            k: (v() if callable(v) else v) for k, v in DECK_FIELDS.items()
        }

    def _bar_action(self, tab: int, action) -> None:
        # A control of a tab's bar was used: that tab plays.
        self._activate(tab)
        action()

    def _user(self, action) -> None:
        # A menu command or shortcut: it works on the tab that is shown.
        self._activate(self.tabs.currentIndex())
        action()

    def _activate(self, tab: int) -> None:
        # The tab's bar takes over the sound.  What the tab that played was
        # playing is kept, paused where it was; this tab's own song comes back
        # paused where it was left (or nothing, if it was not playing).
        if tab == self.active:
            return
        old = {k: getattr(self, k) for k in DECK_FIELDS if k != "position"}
        old["position"] = self.player.position() if self._sound else 0
        self.decks[self.active] = old
        self._switching = True
        try:
            if self._sound:
                self.player.pause() if (
                    self.player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState
                ) else None
            self.bars[self.active].play_button.setText("▶")
            self.active = tab
            new = self.decks[tab]
            for k in DECK_FIELDS:
                if k != "position":
                    setattr(self, k, new[k])
            self.model.set_playing(self.playing)
            if self.playing is not None and self._sound is not None:
                self.player.setSource(
                    QUrl.fromLocalFile(self.full_path(self.playing))
                )
                self.player.pause()
                self._pending_seek = new["position"]
            elif self._sound:
                self.player.stop()
                self._pending_seek = None
            QTimer.singleShot(0, self._apply_volume)
            self._update_rating_bar()
            self.play_button.setText("▶")
            self._update_shuffle_lock()
        finally:
            self._switching = False

    def _volume_moved(self, tab: int, value: int) -> None:
        for i, bar in enumerate(self.bars):
            if i != tab and bar.volume.value() != value:
                bar.volume.blockSignals(True)
                bar.volume.setValue(value)
                bar.volume.blockSignals(False)
        self._apply_volume()

    def _mirror_shuffle(self) -> None:
        # One shuffle setting: every bar's button shows it.
        mine = self.shuffle_button
        for bar in self.bars:
            if bar.shuffle_button is mine:
                continue
            bar.shuffle_button.blockSignals(True)
            bar.shuffle_button.setChecked(mine.isChecked())
            bar.shuffle_button.setEnabled(mine.isEnabled())
            bar.shuffle_button.setToolTip(mine.toolTip())
            bar.shuffle_button.blockSignals(False)

    def _tab_changed(self, index: int) -> None:
        self._update_status()
        self._layout_timer.start()

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

    def _bulk_value(self, mode: str, key: str) -> None:
        # Add a value to a tag (a genre, say) in every selected track, or
        # remove one, leaving their other values alone; stored at once.
        if not self.current or not self._confirm_discard():
            return
        paths = list(self.current)
        if change_value(
            self, self.db, paths, mode, key, self._suggest(key), self._store
        ):
            self.editor.set_files(
                [self.db.tags[p] for p in paths], self.root, paths
            )

    def _store(self, paths: list[str], changes_for) -> None:
        try:
            self.db.write(paths, changes_for)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return
        self.model.refresh(paths)
        self._resort({k for p in paths for k in changes_for(p)})
        self._update_status()

    def _resort(self, keys: set) -> None:
        # Sorting and filtering all the tracks again takes most of a second,
        # so only when the change can move a row: the table is sorted by one
        # of the changed tags, or a search is active.
        for lst in self.lists:
            column = lst.proxy.sortColumn()
            sorted_by = (
                COLUMNS[column][1] if 0 <= column < len(COLUMNS) else None
            )
            if lst.proxy.needles or sorted_by in keys:
                lst.proxy.invalidate()

    def _update_status(self) -> None:
        shown, total = self.proxy.rowCount(), self.model.rowCount()  # (shown tab)
        self.status_label.setText(
            f"{shown} of {total} tracks"
            if shown != total
            else f"{total} tracks"
        )

    # -- playback ----------------------------------------------------------

    def path_at(self, row: int, lst=None) -> str:
        proxy = (lst or self.lst).proxy
        return self.model.paths[proxy.mapToSource(proxy.index(row, 0)).row()]

    def play_index(self, index, lst) -> None:
        self._activate({0: 0, 1: 2, 2: SAMPLES_TAB}[self.lists.index(lst)])
        self.errors_in_a_row = 0
        self._end_schedule()
        self.queue, self.queue_playlist = (
            None,
            None,
        )  # playing from the library view again
        self.play_row(index.row(), True, lst)

    def play_row(self, row: int, record: bool = True, lst=None) -> None:
        # Play the track at this row of the (sorted, filtered) view.
        lst = lst or self.play_lst
        if 0 <= row < lst.proxy.rowCount():
            self.play_path(self.path_at(row, lst), record)

    def play_path(self, path: str, record: bool = True) -> None:
        self._update_shuffle_lock()
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
        # (trying out a snippet is not listening: no skip, no play counted)
        self.skippable = self.active != SAMPLES_TAB
        if self.active == SAMPLES_TAB and path != self.playing:
            self.seek.reset_region()  # a new track: all of it
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
                lst = self.play_lst
                lst.table.scrollTo(lst.proxy.index(row, 0))
        full = self.full_path(path)
        if not os.path.exists(full):
            self._skip(f"Missing file: {path}")
            return
        self.player.setSource(QUrl.fromLocalFile(full))
        self._apply_volume()
        self.stalled_ms, self.start_kicks = 0, 0
        self.player.play()
        tags = self.db.tags[path]
        title = self.model.text(path, 0)
        artist = SEPARATOR.join(tags.get("ARTIST", []))
        self.now_playing.setText(f"{title} — {artist}" if artist else title)
        self._update_rating_bar()
        self._update_samples_bar()
        self.statusBar().clearMessage()

    def _row_in_view(self, path: str) -> int:
        # Row of this track in the current view, or -1 (e.g. it was filtered
        # out).
        index = self.play_lst.proxy.mapFromSource(
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
        lst = self.play_lst
        return [self.path_at(r, lst) for r in range(lst.proxy.rowCount())]

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

    def _update_shuffle_lock(self) -> None:
        # A dynamic playlist plays in its own order: shuffling it makes little
        # sense, so the button is off and disabled while one plays and the
        # choice comes back when anything else plays.
        if self.schedule is not None and self.schedule.section is not None:
            locked = self.db.holds_dynamic(self.schedule.section.container_id)
        else:
            locked = self.queue_playlist is not None and self.db.holds_dynamic(
                self.queue_playlist
            )
        self._shuffle_locking = True
        try:
            self.shuffle_button.setEnabled(not locked)
            self.shuffle_action.setEnabled(not locked)
            self.shuffle_button.setChecked(self.shuffle_wanted and not locked)
            self.shuffle_button.setToolTip(
                "A dynamic playlist plays in order"
                if locked
                else "Random order (Ctrl+R): no track repeats until all have"
                " played"
            )
        finally:
            self._shuffle_locking = False
        self._mirror_shuffle()

    def _shuffle_toggled(self, on: bool) -> None:
        if self._shuffle_locking:
            self.shuffle_action.setChecked(on)
            return
        self.shuffle_wanted = on
        self.shuffle_action.setChecked(on)
        self.shuffle_button.setChecked(on)
        self._mirror_shuffle()
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
        floor = self.seek.region()[0] if self.active == SAMPLES_TAB else 0
        if self.player.position() > floor + 3000:
            self.player.setPosition(floor)
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
        self._activate(1)
        if self.db.kinds().get(playlist_id) in ("container", "sequence"):
            self.play_schedule(playlist_id, start_row, shuffle)
            return
        self._end_schedule()
        paths = [p for _e, p in self.db.playlist_entries(playlist_id)]
        if not paths:
            return
        if shuffle is not None and not self.db.holds_dynamic(playlist_id):
            self.shuffle_button.setChecked(shuffle)
        self.queue, self.queue_playlist, self.queue_pos = paths, playlist_id, 0
        self.errors_in_a_row = 0
        self.heard, self.history = set(), []
        self._update_shuffle_lock()
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
        if shuffle is not None and not self.db.holds_dynamic(playlist_id):
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
                self._update_shuffle_lock()
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

    def _table_menu(self, lst, pos) -> None:
        table = lst.table
        index = table.indexAt(pos)
        if index.isValid() and not table.selectionModel().isRowSelected(
            index.row()
        ):
            table.selectRow(index.row())
        paths = self._selected_files(lst)
        menu = QMenu(self)
        populate_add_menu(menu, self.db, paths, self, self._added_to_playlist)
        if paths:
            menu.addSeparator()
            menu.addAction(
                "Detect BPM", lambda: self.analyse(detect_bpm, paths)
            )
            menu.addAction(
                "Measure loudness",
                lambda: self.analyse(measure_loudness, paths),
            )
        menu.exec(table.viewport().mapToGlobal(pos))

    def analyse(self, action, paths: list[str]) -> None:
        # Detect the BPM of, or measure the loudness of, these files; stored in
        # the database only.
        if not self._confirm_discard():
            return
        changed = action(self, self.db, paths)
        if not changed:
            return
        self.model.refresh(changed)
        self.editor.set_files(
            [self.db.tags[p] for p in self.current], self.root, self.current
        )

    def _added_to_playlist(self, playlist_id: int) -> None:
        self.playlist_panel.refresh(select=playlist_id)
        self._playlist_changed(playlist_id)
        self.statusBar().showMessage("Added to playlist", 4000)

    def toggle(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            if self.active == SAMPLES_TAB:
                self._enter_region()
            self.player.play()
        else:
            self.errors_in_a_row = 0
            if self.active == 1:
                # in the Playlists tab Play starts the selected playlist
                if self.playlist_panel.play_selected():
                    return
            if self.queue:
                self._play_at(min(max(self.queue_pos, 0), len(self.queue) - 1))
                return
            rows = self.play_lst.table.selectionModel().selectedRows()
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
            if self.active == SAMPLES_TAB:
                self._region_done()
            else:
                self.next()
        elif status == QMediaPlayer.MediaStatus.BufferedMedia:
            self.errors_in_a_row = 0
        if (
            self._pending_seek is not None
            and status
            in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
        ):
            # the song of a tab that was paused comes back where it was
            self.player.setPosition(self._pending_seek)
            self._pending_seek = None

    def _player_error(self, _error, message: str) -> None:
        self._skip(f"Cannot play {self.playing}: {message}")

    def _state_changed(self, *_) -> None:
        if self._switching:
            return
        state = (
            self.player.playbackState()
            if self._sound
            else QMediaPlayer.PlaybackState.StoppedState
        )  # no output yet: nothing plays
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("⏸" if playing else "▶")
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._finish_track()
            if self.playing is None:
                self.now_playing.setText("Nothing playing")
                self._update_rating_bar()

    def _count_listening(self, position: int) -> None:
        # Add up the time actually heard; once it passes the threshold, record
        # the play (once per start).
        if self.active == SAMPLES_TAB:
            self.last_position = None
            return
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

    def _check_stalled_start(self) -> None:
        # The player says it plays, the position stays at 0 and no sound comes:
        # so far MP3 files with both a cover picture and a LAME/Xing header.
        # Moving the position, to 0 as well, makes the player read the file
        # again and it plays.
        if (
            self.player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState
            or self.player.position() > 0
        ):
            self.stalled_ms = 0
            return
        self.stalled_ms += self.stall_timer.interval()
        if self.stalled_ms < 1500 or self.start_kicks >= 3:
            return
        self.start_kicks += 1
        self.stalled_ms = 0
        self.player.setPosition(0)

    def _position_changed(self, position: int) -> None:
        self._count_listening(position)
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        if self.active == SAMPLES_TAB and not self._switching:
            self._keep_in_region(position)
        self.time_label.setText(
            f"{fmt_time(position)} / {fmt_time(self.player.duration())}"
        )

    # -- the Samples tab ---------------------------------------------------

    def _samples_path(self) -> str | None:
        # The track of the Samples tab, whether or not that tab plays now.
        if self.active == SAMPLES_TAB:
            return self.playing
        return self.decks[SAMPLES_TAB]["playing"]

    def _update_samples_bar(self) -> None:
        bar = self.bars[SAMPLES_TAB]
        start, end = bar.seek.region()
        bar.region_label.setText(
            f"Snippet length {clock(end - start)}   (from {clock(start)}"
            f" to {clock(end)})"
            if self._samples_path() is not None
            else "Play a track, then drag the two arrows to choose a snippet"
        )
        bar.save_button.setEnabled(
            self._samples_path() is not None
            and end - start >= snippet.MIN_LENGTH_MS
        )

    def _region_released(self) -> None:
        # A marker was let go: the music goes inside the region.
        if self.active == SAMPLES_TAB and self._sound:
            self._enter_region()

    def _enter_region(self) -> None:
        start, end = self.seek.region()
        position = self.player.position()
        if position < start or position >= end:
            self.player.setPosition(start)

    def _keep_in_region(self, position: int) -> None:
        # Only the region plays: at its end the music pauses at the start of
        # the region, ready to play it again.
        if (
            self.player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState
        ):
            return
        start, end = self.seek.region()
        if position >= end:
            self._stop_region()
        elif position < start:
            self.player.setPosition(start)

    def _stop_region(self) -> None:
        # Pause at the end of the region; from the event loop, not inside
        # the handler of the player's own signal (and once, though the player
        # reports its position several times before it has paused).
        if not self._region_stopping:
            self._region_stopping = True
            QTimer.singleShot(0, self._pause_at_start)

    def _pause_at_start(self) -> None:
        # The bar goes back to the start of the region; the player is only
        # paused (a seek straight after a pause is what the Samples tab did
        # that no other tab does).  Play moves it to the start (see
        # _enter_region).
        self._region_stopping = False
        if self.active == SAMPLES_TAB and self._sound:
            self.player.pause()
            self.seek.setValue(self.seek.region()[0])

    def _region_done(self) -> None:
        # The track ended inside the region (the end marker is at its end).
        self._stop_region()

    def save_snippet(self) -> None:
        path = self._samples_path()
        start, end = self.bars[SAMPLES_TAB].seek.region()
        if path is None or end - start < snippet.MIN_LENGTH_MS:
            self.statusBar().showMessage(
                "Play a track in the Samples tab and move the two arrows"
                " apart first.",
                8000,
            )
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rel, playlist = snippet.save(self.db, self.root, path, start, end)
        except snippet.SnippetError as e:
            QMessageBox.warning(self, "Cannot save the snippet", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.model.add_path(rel)
        # (the Playlists tab shows the playlist, so Play there plays it)
        self.playlist_panel.refresh(select=playlist)
        self._playlist_changed(playlist)
        self._update_status()
        self.statusBar().showMessage(
            f"Saved {rel} and added it to the playlist {snippet.PLAYLIST}.",
            10000,
        )

    # -- tag editing -------------------------------------------------------

    def _selected_files(self, lst=None) -> list[str]:
        lst = lst or self.lst
        rows = sorted(
            i.row() for i in lst.table.selectionModel().selectedRows()
        )
        return [self.path_at(r, lst) for r in rows]

    def _selection_changed(self, lst) -> None:
        # Only the Tags tab's list drives the tag editor; the Library tab's
        # selection is for rating and the playlist menu.
        if lst is not self.lists[1]:
            return
        if self._quiet:
            return
        if not self._confirm_discard():
            self._quiet = True
            lst.table.selectionModel().select(
                lst.last_selection,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self._quiet = False
            return
        lst.last_selection = QItemSelection(
            lst.table.selectionModel().selection()
        )
        self.current = self._selected_files(lst)
        self.editor.set_files(
            [self.db.tags[p] for p in self.current], self.root, self.current
        )
        n = len(self.current)
        self.summary.setText(
            "No tracks selected"
            if n == 0
            else f"Editing {n} track{'s' if n != 1 else ''}"
            + (f": {os.path.basename(self.current[0])}" if n == 1 else "")
        )

    def rate_selection(self, rating) -> None:
        # The Rating menu and Ctrl+0..5: the songs selected in the shown tab.
        saved, self.current = self.current, self._selected_files()
        try:
            self.set_rating(rating)
        finally:
            self.current = saved

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

    def _rate_cell(self, index, rating, lst) -> None:
        # A rating cell was clicked: rate that track (the Rating menu and
        # Ctrl+0..5 rate the whole selection).
        saved, self.current = self.current, [self.path_at(index.row(), lst)]
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
        self._resort(set(changes))
        self.editor.set_files(
            [self.db.tags[p] for p in paths], self.root, paths
        )
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
        add(menu, "Save S&nippet", None, self.save_snippet)
        add(menu, "S&ync with Phone...", None, self.sync_phone)
        add(menu, "&Review Phone Changes...", None, self.review_phone)
        add(menu, "&Quit", QKeySequence.StandardKey.Quit, self.close)
        menu = self.menuBar().addMenu("&View")
        for number, name in enumerate(
            ("Library", "Playlists", "Tags", "Samples")
        ):
            add(
                menu,
                f"Show {name}",
                f"Ctrl+{number + 1}",
                lambda _checked=False, n=number: self.tabs.setCurrentIndex(n),
            )
        menu.addSeparator()
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
        add(
            menu,
            "Not rated",
            "Ctrl+Backspace",
            lambda: self.rate_selection(None),
        )
        for n in range(6):
            add(
                menu,
                f"{n} star{'s' if n != 1 else ''}  "
                + "\u2605" * n
                + "\u2606" * (5 - n),
                f"Ctrl+{n}",
                lambda _=False, n=n: self.rate_selection(n),
            )
        menu = self.menuBar().addMenu("&Playback")
        # (these work on the tab that is shown)
        add(menu, "&Play / Pause", "Ctrl+Space", lambda: self._user(self.toggle))
        add(menu, "&Next", "Ctrl+Right", lambda: self._user(self.next))
        add(menu, "P&revious", "Ctrl+Left", lambda: self._user(self.previous))
        add(menu, "&Stop", "Ctrl+.", lambda: self._user(self.stop))
        add(
            menu,
            "Next &section",
            "Ctrl+Shift+Right",
            lambda: self._user(self.next_section),
        )
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
        # track (the output cannot go above full).  Until the output exists
        # the volume is applied when it is made.
        if self._sound is None:
            return
        gain = (
            self.db.gain_db(self.playing, self.leveling, self.leveling_target)
            if self.playing
            else 0.0
        )
        volume = (
            min(1.0, self.volume.value() / 100 * 10 ** (gain / 20)) * self.fade
        )
        # (Qt's audio output can stop responding when its volume is set while
        # the player is being switched; so it is set only when it changes)
        if (
            self._volume_applied is not None
            and abs(volume - self._volume_applied) < 1e-4
        ):
            return
        self._volume_applied = volume
        self.audio.setVolume(volume)

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
        for lst in self.lists:
            lst.proxy.set_show_duplicates(on)
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
        for lst in self.lists:
            lst.proxy.setSourceModel(self.model)
            lst.last_selection = QItemSelection()
        self.playlist_panel.set_db(self.db)
        self.current = []
        self.editor.set_files([])
        self._quiet = False
        self._update_status()

    def sync_phone(self) -> None:
        if not self._confirm_discard():
            return
        self.phone_report = None
        open_sync_dialog(self, self.db, self._phone_synced)
        self.review_phone()

    def review_phone(self) -> None:
        # Rate or correct the songs the last sync took from the phone.
        report = getattr(self, "phone_report", None)
        if not report or not report.changes:
            self.statusBar().showMessage(
                "The last sync took nothing from the phone"
                if report
                else "No phone sync to review",
                4000,
            )
            return
        review_phone_changes(self, self.db, report, self.model.refresh)

    def _phone_synced(self, report) -> None:
        # The phone's plays and ratings are in the database: show them, follow
        # the dynamic playlists that were refilled.
        self.phone_report = report
        self.model.refresh(report.paths)
        self.playlist_panel.refresh()
        for pid in report.playlists_changed:
            self._playlist_changed(pid)

    def _column_layout(self, lst=None) -> dict:
        # The table's columns as they are now: order, hidden ones, and the width
        # of each shown one (by column key).
        table = (lst or self.lst).table
        header = table.horizontalHeader()
        order = sorted(range(len(COLUMNS)), key=header.visualIndex)
        return {
            "order": [COLUMNS[i][1] for i in order],
            "hidden": [
                COLUMNS[i][1] for i in order if table.isColumnHidden(i)
            ],
            "widths": {
                COLUMNS[i][1]: header.sectionSize(i)
                for i in order
                if not table.isColumnHidden(i)
            },
        }

    def _saved_columns(
        self, settings: QSettings, key: str = "column_layout"
    ) -> dict:
        try:
            layout = json.loads(settings.value(key, "") or "null")
            if isinstance(layout, dict):
                return layout
        except (TypeError, ValueError):
            pass
        if key != "column_layout":
            return {}
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

    def _apply_columns(self, layout: dict, lst=None) -> None:
        # Show the columns as saved; columns the saved layout does not know
        # (added since) go last, hidden or not as by default.
        table = (lst or self.lst).table
        header = table.horizontalHeader()
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
            table.setColumnHidden(i, hidden)
            width = layout.get("widths", {}).get(key)
            if width and not hidden and key != "@rating":
                table.setColumnWidth(i, width)
        if known and not any(
            not table.isColumnHidden(i) for i in range(len(keys))
        ):
            table.setColumnHidden(
                0, False
            )  # (a saved layout must never leave nothing to see)

    def _restore_layout(self) -> None:
        settings = QSettings("music-player", "player")
        geometry = settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.tabs.setCurrentIndex(
            min(max(settings.value("tab", 0, type=int), 0), SAMPLES_TAB)
        )
        self.shuffle_button.setChecked(
            settings.value("shuffle", False, type=bool)
        )
        self.duplicates_action.setChecked(
            settings.value("show_duplicates", False, type=bool)
        )
        for lst in self.lists:
            lst.proxy.set_show_duplicates(self.duplicates_action.isChecked())
        self.leveling_target = settings.value(
            "leveling_target", self.leveling_target, type=float
        )
        mode = settings.value("leveling", "track")
        self._set_leveling(
            mode if mode in ("off", "track", "album") else "track"
        )  # on (track mode) unless the user chose otherwise
        self._apply_columns(self._saved_columns(settings), self.lists[0])
        self._apply_columns(
            self._saved_columns(settings, "tags_column_layout"), self.lists[1]
        )
        self._apply_columns(
            self._saved_columns(settings, "samples_column_layout"),
            self.lists[2],
        )
        if settings.contains(
            "volume"
        ):  # (value() with a type gives 0 when the setting is missing)
            self.volume.setValue(settings.value("volume", 70, type=int))

    def _save_layout(self) -> None:
        settings = QSettings("music-player", "player")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("tab", self.tabs.currentIndex())
        settings.setValue(
            "column_layout", json.dumps(self._column_layout(self.lists[0]))
        )
        settings.setValue(
            "tags_column_layout",
            json.dumps(self._column_layout(self.lists[1])),
        )
        settings.setValue(
            "samples_column_layout",
            json.dumps(self._column_layout(self.lists[2])),
        )
        settings.remove("columns")  # the old, count-dependent format
        settings.remove("column_count")
        settings.setValue("volume", self.volume.value())
        settings.setValue("shuffle", self.shuffle_wanted)
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
    # When the window stops responding, `kill -USR1 <pid>` prints on the
    # terminal where each thread of the player is.
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    app = QApplication([sys.argv[0]] + qt_args)
    window = MainWindow(TagDb(path))
    window.show()
    QTimer.singleShot(0, window.tidy_playlists)
    QTimer.singleShot(100, window._make_sound)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
