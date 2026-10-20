#!/usr/bin/env python3
# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The database editor: browse the library and edit its tags.
#
# Select files (or whole folders, or every file that has a tag value from the
# Tags tab) and edit
# their tags together.  Only the database is changed; the media files are not
# touched.
#
# usage: tagdb_editor.py --database DB        (the database is required)
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time

from PyQt6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QSettings,
    Qt,
    QTimer,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QKeySequence,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import tagio
import dynamic_playlists
from playlists import PlaylistPanel, populate_add_menu
from tag_editor import TagEditor
from tagdb import (
    LIST_SEPARATORS,
    MAX_CHOICES,
    NUMERIC_TAGS,
    SEPARATOR,
    SPLIT_TAGS,
    STANDARD_TAGS,
    TagDb,
    missing_tags,
    normalize,
    stat_text,
)
from rating_delegate import (
    RATING_ROLE,
    SORT_ROLE,
    STAR_MARGIN,
    STAR_SLOT,
    RatingDelegate,
)
from path_display import PathDelegate, ROOT_ROLE, full_path
from phone_sync_dialog import open_sync_dialog

PATH_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 3
VALUE_ROLE = Qt.ItemDataRole.UserRole + 4
VARIANT_HUES = (
    0,
    40,
    120,
    200,
    270,
    320,
)  # background hues cycled over the groups of variant spellings
ALBUM_ROLE = Qt.ItemDataRole.UserRole + 5  # set on the album rows under a value
TREE_COLUMNS = [
    ("Name", None),
    ("Title", "TITLE"),
    ("Artist", "ARTIST"),
    ("Album", "ALBUM"),
    ("Genre", "GENRE"),
    ("BPM", "BPM"),
    ("Track", "TRACKNUMBER"),
    ("Year", "DATE"),
    ("Album artist", "ALBUMARTIST"),
    ("Rating", "@rating"),
    ("Plays", "@plays"),
    ("Last played", "@last"),
    ("Format", "@format"),
    ("Duplicate", "@dup"),
    ("Pathname", "@path"),
    ("Missing", ""),
]  # the "" column lists the standard tags the file lacks


def value_label(value: str) -> str:
    # How a tag value is shown: blank values get a visible stand-in.
    if value == "":
        return "<empty>"
    if not value.strip():
        return f"<{len(value)} space{'s' if len(value) != 1 else ''}>"
    return value


class MainWindow(QMainWindow):
    def __init__(self, db: TagDb):
        super().__init__()
        self.resize(1250, 720)
        self.db = db
        # dynamic playlists that are short (a longer length, removed entries)
        # are filled
        self.db.top_up_dynamic()
        self.current: list[str] = []
        self.cells: dict[str, list[QStandardItem]] = {}
        self._quiet = False
        self._root = ""  # the music root, as the database records it
        self._last_selection = QItemSelection()

        self.model = QStandardItemModel(0, len(TREE_COLUMNS), self)
        self.model.setHorizontalHeaderLabels([c[0] for c in TREE_COLUMNS])
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tree.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setColumnWidth(0, 320)
        self.rating_column = next(
            i for i, c in enumerate(TREE_COLUMNS) if c[1] == "@rating"
        )
        self.tree.setItemDelegateForColumn(
            self.rating_column, RatingDelegate(self._rate, self.tree)
        )
        self.tree.setColumnWidth(
            self.rating_column, 2 * STAR_MARGIN + 5 * STAR_SLOT
        )
        self.tree.selectionModel().selectionChanged.connect(
            self._selection_changed
        )
        self.path_column = next(
            i for i, c in enumerate(TREE_COLUMNS) if c[1] == "@path"
        )
        self.tree.setItemDelegateForColumn(
            self.path_column, PathDelegate(self.tree)
        )
        self._setup_columns()

        self.overview = QTreeWidget()
        self.overview.setHeaderLabels(["Tag / value", "Files", "Spellings"])
        self.overview.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.overview.setUniformRowHeights(True)
        self.overview.setColumnWidth(0, 380)
        self.overview.itemExpanded.connect(self._overview_expanded)
        self.overview.itemSelectionChanged.connect(self._overview_selected)
        self.overview.itemChanged.connect(self._overview_edited)
        self.overview_filter = QLineEdit()
        self.overview_filter.setPlaceholderText("Filter tags and values...")
        self.overview_filter.setClearButtonEnabled(True)
        self.overview_timer = QTimer(self)
        self.overview_timer.setSingleShot(True)
        self.overview_timer.setInterval(300)
        self.overview_timer.timeout.connect(self._refresh_overview)
        self.overview_filter.textChanged.connect(
            lambda _: self.overview_timer.start()
        )
        self.variants_only = QCheckBox("Only variant spellings")
        self.variants_only.setToolTip(
            "Show only values that are spelled differently in different files, "
            "e.g. 'A and his orchestra' / 'A & His Orchestra'"
        )
        self.variants_only.toggled.connect(
            lambda _: self.overview_timer.start()
        )
        self.lists_only = QCheckBox("Only lists to split")
        self.lists_only.setToolTip(
            "Show only values of multi-valued tags (artist, genre, ...) that"
            " contain ',' or '/', which probably should be several"
            " values.\nRename the value using '; ' between the parts to"
            " split it."
        )
        self.lists_only.toggled.connect(lambda _: self.overview_timer.start())
        self._variant_cache: dict[str, dict[str, int]] = {}
        self.overview_index: dict[str, dict[str, list[str]]] = {}
        self.overview_stale = True
        overview_page = QWidget()
        page_layout = QVBoxLayout(overview_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.overview_filter, 1)
        filter_row.addWidget(self.variants_only)
        filter_row.addWidget(self.lists_only)
        page_layout.addLayout(filter_row)
        page_layout.addWidget(self.overview)

        self.missing_only = QCheckBox("Only files missing standard tags")
        self.missing_only.setToolTip(
            "Show only files lacking one of: "
            + ", ".join(STANDARD_TAGS)
            + "\n(a tag with only blank values counts as missing)"
        )
        self.missing_only.toggled.connect(
            lambda _: self._missing_tags_changed()
        )
        self.missing_count = QLabel()
        saved = QSettings("file-tags", "tagdb_editor").value("missing_tags")
        chosen = (
            set(saved) if isinstance(saved, list) else set(STANDARD_TAGS)
        )  # tags that count as missing
        self.tag_checks: dict[str, QCheckBox] = {}
        tag_row = QHBoxLayout()
        tag_row.addSpacing(18)
        for key in STANDARD_TAGS:
            box = QCheckBox(key.title())
            box.setChecked(key in chosen)
            box.setToolTip(f"Show files that lack {key}")
            box.toggled.connect(lambda _: self._missing_tags_changed())
            self.tag_checks[key] = box
            tag_row.addWidget(box)
        tag_row.addStretch()
        self.file_search = QLineEdit()
        self.file_search.setPlaceholderText(
            "Search files: every word must match the path or any tag value..."
        )
        self.file_search.setClearButtonEnabled(True)
        self.file_search_timer = QTimer(self)
        self.file_search_timer.setSingleShot(True)
        self.file_search_timer.setInterval(300)
        self.file_search_timer.timeout.connect(self._apply_missing_filter)
        self.file_search.textChanged.connect(
            lambda _: self.file_search_timer.start()
        )
        self._haystacks: dict[str, str] = {}
        files_page = QWidget()
        files_layout = QVBoxLayout(files_page)
        files_layout.setContentsMargins(0, 0, 0, 0)
        missing_row = QHBoxLayout()
        missing_row.addWidget(self.missing_only)
        missing_row.addStretch()
        missing_row.addWidget(self.missing_count)
        files_layout.addWidget(self.file_search)
        files_layout.addLayout(missing_row)
        files_layout.addLayout(tag_row)
        files_layout.addWidget(self.tree)

        self.tabs = QTabWidget()
        self.tabs.addTab(files_page, "Files")
        self.tabs.addTab(overview_page, "Tags")
        self.tabs.addTab(self._make_duplicates_page(), "Duplicates")
        self.playlist_panel = PlaylistPanel(db)
        self.tabs.addTab(self.playlist_panel, "Playlists")
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tabs.currentChanged.connect(
            lambda _: (self._refresh_overview(), self._refresh_duplicates())
        )

        self.summary = QLabel("No files selected")
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

        right = QWidget()
        layout = QVBoxLayout(right)
        layout.addWidget(self.summary)
        layout.addWidget(self.editor, 1)
        layout.addLayout(buttons)

        self.splitter = QSplitter()
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self.splitter)
        self._restore_layout()
        # Also saved shortly after any resize or move, so a window that is
        # killed rather than closed
        # (or a crash) still remembers its size.
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(500)
        self._layout_timer.timeout.connect(self._save_layout)
        self.splitter.splitterMoved.connect(
            lambda *_: self._layout_timer.start()
        )

        self._make_actions()
        self.load()

    def _suggest(self, key: str) -> list[str]:
        # Existing values to pick from when editing a tag; none if there are too
        # many to be a menu.
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

    # -- loading -----------------------------------------------------------

    def _make_actions(self) -> None:
        menu = self.menuBar().addMenu("&File")
        for text, shortcut, slot in (
            ("&Open Database...", QKeySequence.StandardKey.Open, self.open_db),
            ("S&can for New Files...", None, self.scan_for_files),
            ("&Save", QKeySequence.StandardKey.Save, self.save),
            ("S&ync with Phone...", None, self.sync_phone),
            ("&Quit", QKeySequence.StandardKey.Quit, self.close),
        ):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            if text == "&Save":
                self.save_action = action
        menu = self.menuBar().addMenu("&Rating")
        self._add_rating_actions(menu)

    def _add_rating_actions(self, menu) -> None:
        # Entries that rate the selected files (a folder stands for the files in
        # it): not rated, 0 to 5 stars.
        menu.addAction("Not rated", lambda: self.set_rating(None)).setShortcut(
            "Ctrl+Backspace"
        )
        for n in range(6):
            menu.addAction(
                f"{n} star{'s' if n != 1 else ''}  "
                + "\u2605" * n
                + "\u2606" * (5 - n),
                lambda n=n: self.set_rating(n),
            ).setShortcut(f"Ctrl+{n}")

    def set_rating(self, rating) -> None:
        # Rate the selected files (0 to 5, None = not rated).  Stored at once,
        # whatever tag edits are pending.
        paths = self._selected_files()
        if not paths:
            self.statusBar().showMessage(
                "Select files or folders to rate", 4000
            )
            return
        label = (
            "not rated"
            if rating is None
            else f"{rating} star{'s' if rating != 1 else ''}"
        )
        if (
            len(paths) > 25
            and QMessageBox.question(
                self,
                "Rate files",
                f"Rate {len(paths)} files as {label}?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.db.set_ratings(paths, rating)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return
        self.tree.setUpdatesEnabled(False)
        for p in paths:
            self._fill_cells(p)
        self.tree.setUpdatesEnabled(True)
        self.statusBar().showMessage(
            f"{len(paths)} file{'s' if len(paths) != 1 else ''} rated {label}",
            4000,
        )

    def _tree_menu(self, pos) -> None:
        index = self.tree.indexAt(pos)
        if index.isValid() and not self.tree.selectionModel().isSelected(index):
            self.tree.setCurrentIndex(index)
        menu = QMenu(self)
        populate_add_menu(
            menu,
            self.db,
            self._selected_files(),
            self,
            lambda pid: self.playlist_panel.refresh(select=pid),
        )
        files = self._selected_files()
        if files:
            menu.addSeparator()
            self._add_rating_actions(menu.addMenu("Rate"))
            menu.addAction(
                f"Remove {len(files)} file{'s' if len(files) != 1 else ''} from"
                " library...",
                lambda: self.remove_from_library(files),
            )
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def remove_from_library(self, files: list[str]) -> None:
        # Delete the selected files from the database (never from the disk),
        # after a backup and with a journal.
        if not self._confirm_discard():
            return
        plays = sum(
            1
            for p in files
            if self.db.stats.get(p, (None, 0, None))[1]
            or self.db.stats.get(p, (None,))[0] is not None
        )
        answer = QMessageBox.question(
            self,
            "Remove from library",
            f"Remove {len(files)} file{'s' if len(files) != 1 else ''} from the"
            " database?\n\nTheir tags, play counts, ratings and playlist"
            f" entries are deleted ({plays} of them have plays or a rating)."
            " The files on disk are not touched; a later scan will list them"
            " as new again if they are still in the music folder.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        backup = self.db.backup("remove-files")
        journal = self.db.journal(
            "remove-files",
            [
                {
                    "action": "remove",
                    "path": p,
                    "tags": self.db.tags[p],
                    "stats": list(self.db.stats.get(p, ())),
                }
                for p in files
            ],
        )
        self.db.remove_files(files)
        left = [p for p in files if p in self.db.tags]
        self.playlist_panel.set_db(self.db)
        self.load()
        QMessageBox.information(
            self,
            "Remove from library",
            f"Removed {len(files) - len(left)}"
            f" files{f' ({len(left)} FAILED)' if left else ''}.\n\nBackup:"
            f" {backup}\nJournal: {journal}",
        )

    def scan_for_files(self) -> None:
        # File > Scan for New Files: add, remove and re-read files after the
        # music folder changed.
        if not self._confirm_discard():
            return
        from scan_dialog import scan_library

        if scan_library(self, self.db, normalize):
            self.playlist_panel.set_db(self.db)
            self.load()

    def _setup_columns(self) -> None:
        # Click a column heading to sort the files of each folder by it (again
        # to reverse); drag headings to reorder;
        # right-click a heading to choose the columns shown (the choice is
        # remembered).
        self.model.setSortRole(SORT_ROLE)
        header = self.tree.header()
        header.setSectionsClickable(True)
        header.setSectionsMovable(True)
        header.setSortIndicatorShown(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._column_menu)
        self.tree.setSortingEnabled(True)
        header.setSortIndicator(
            -1, Qt.SortOrder.AscendingOrder
        )  # in the database's order until a heading is clicked
        hidden = QSettings("file-tags", "tagdb_editor").value("hidden_columns")
        if not isinstance(hidden, list):
            hidden = [
                "Track",
                "Year",
                "Album artist",
            ]  # shown at first; the others are chosen from the menu
        for i, (title, _key) in enumerate(TREE_COLUMNS):
            self.tree.setColumnHidden(i, i > 0 and title in hidden)
            if i != self.rating_column:
                self.tree.setColumnWidth(
                    i,
                    (
                        320
                        if i == 0
                        else (
                            60
                            if title in ("BPM", "Track", "Year")
                            else 480 if title == "Pathname" else 140
                        )
                    ),
                )

    def _column_menu(self, pos) -> None:
        menu = QMenu(self)
        for i, (title, _key) in enumerate(TREE_COLUMNS):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(not self.tree.isColumnHidden(i))
            action.setEnabled(i > 0)  # the name column stays
            action.toggled.connect(
                lambda shown, col=i: self._show_column(col, shown)
            )
        menu.exec(self.tree.header().mapToGlobal(pos))

    def _show_column(self, column: int, shown: bool) -> None:
        self.tree.setColumnHidden(column, not shown)
        QSettings("file-tags", "tagdb_editor").setValue(
            "hidden_columns",
            [
                t
                for i, (t, _k) in enumerate(TREE_COLUMNS)
                if self.tree.isColumnHidden(i)
            ]
            or [""],
        )

    def sync_phone(self) -> None:
        if not self._confirm_discard():
            return
        open_sync_dialog(self, self.db, self._phone_synced)

    def _phone_synced(self, report) -> None:
        # The phone's plays and ratings are in the database: show them.
        self.load()
        self.playlist_panel.refresh()

    def open_db(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open tag database",
            os.path.dirname(self.db.path),
            "SQLite (*.db *.sqlite);;All files (*)",
        )
        if path:
            self.db.con.close()
            self.db = TagDb(path)
            self.playlist_panel.set_db(self.db)
            self.load()

    def load(self) -> None:
        # Fill the file tree from the database.
        self._quiet = True
        self.model.removeRows(0, self.model.rowCount())
        self.cells.clear()
        dirs: dict[str, QStandardItem] = {}
        self._root = self.db.root()
        for path in self.db.tags:
            parent = self.model.invisibleRootItem()
            folder = ""
            parts = path.split("/")
            for part in parts[:-1]:
                folder = f"{folder}/{part}" if folder else part
                if folder not in dirs:
                    item = QStandardItem(part)
                    item.setData(None, PATH_ROLE)
                    item.setData(
                        "0" + part.lower(), SORT_ROLE
                    )  # folders before files
                    others = [QStandardItem() for _ in TREE_COLUMNS[1:]]
                    for cell, (_t, key) in zip(others, TREE_COLUMNS[1:]):
                        cell.setData(
                            -1.0 if self._numeric_column(key) else "", SORT_ROLE
                        )
                    parent.appendRow([item] + others)
                    dirs[folder] = item
                parent = dirs[folder]
            cells = [QStandardItem(parts[-1])] + [
                QStandardItem() for _ in TREE_COLUMNS[1:]
            ]
            cells[0].setData(path, PATH_ROLE)
            cells[0].setData("1" + parts[-1].lower(), SORT_ROLE)
            parent.appendRow(cells)
            self.cells[path] = cells
            self._fill_cells(path)
        self._missing_tags_changed()
        self.tree.expandAll()
        self._quiet = False
        self.current = []
        self._last_selection = QItemSelection()
        self.editor.set_files([])
        self._update_summary()
        self.overview_stale = True
        self._refresh_overview()
        self._editor_changed()

    def _fill_cells(self, path: str) -> None:
        tags = self.db.tags[path]
        self._haystacks.pop(
            path, None
        )  # tags changed: rebuild the search text on demand
        for cell, (_title, key) in zip(self.cells[path][1:], TREE_COLUMNS[1:]):
            cell.setData(self._sort_value(path, key), SORT_ROLE)
            if key == "@rating":
                rating = self.db.stats.get(path, (None, 0, None))[0]
                cell.setData(-1 if rating is None else rating, RATING_ROLE)
                cell.setToolTip(
                    "Click to rate (a first click on a not-rated file gives"
                    " zero stars); double-click = not rated"
                )
            if key == "@path":
                cell.setData(self._root, ROOT_ROLE)
                cell.setToolTip(
                    f"Music root: {self._root}\nRelative path: {path}"
                )
            cell.setText(
                full_path(self._root, path)
                if key == "@path"
                else ", ".join(t.lower() for t in missing_tags(tags))
                if key == ""
                else (
                    dynamic_playlists.file_format(path)
                    if key == "@format"
                    else (
                        self._duplicate_text(path)
                        if key == "@dup"
                        else (
                            self._stat_text(path, key)
                            if key in ("@rating", "@plays", "@last")
                            else SEPARATOR.join(tags.get(key, []))
                        )
                    )
                )
            )

    @staticmethod
    def _numeric_column(key) -> bool:
        return key in ("@rating", "@plays") or key in NUMERIC_TAGS

    def _sort_value(self, path: str, key):
        # A number for the numeric columns (what is not a number sorts first),
        # else lower-case text.
        stats = self.db.stats.get(path, (None, 0, None))
        if key == "@rating":
            return -1.0 if stats[0] is None else float(stats[0])
        if key == "@plays":
            return float(stats[1] or 0)
        if key == "@last":
            return stats[2] or ""
        if key == "@format":
            return dynamic_playlists.file_format(path)
        if key == "@dup":
            return self._duplicate_text(path).lower()
        if key == "@path":
            return full_path(self._root, path).lower()
        if key == "":
            return ", ".join(missing_tags(self.db.tags[path])).lower()
        values = self.db.tags[path].get(key, [])
        if key in NUMERIC_TAGS:
            m = re.match(r"\s*(\d+(?:\.\d+)?)", values[0]) if values else None
            return float(m.group(1)) if m else -1.0
        return SEPARATOR.join(values).lower()

    def _stat_text(self, path: str, key: str) -> str:
        return stat_text(self.db.stats.get(path, (None, 0, None)), key)

    def _duplicate_text(self, path: str) -> str:
        if path in self.db.dup_of:
            return "duplicate of " + os.path.basename(self.db.dup_of[path])
        n = len(self.db.copies.get(path, ()))
        return f"primary, {n} cop{'ies' if n != 1 else 'y'}" if n else ""

    def _missing_tags_changed(self) -> None:
        # The 'only files missing' filter or its choice of tags changed.
        for box in self.tag_checks.values():
            box.setEnabled(self.missing_only.isChecked())
        settings = QSettings("file-tags", "tagdb_editor")
        settings.setValue(
            "missing_tags",
            [k for k, b in self.tag_checks.items() if b.isChecked()] or [""],
        )
        self._apply_missing_filter()

    def _apply_missing_filter(self) -> None:
        # Show every file, or only those lacking a standard tag (and the folders
        # that hold them).
        only = self.missing_only.isChecked()
        needles = self.file_search.text().lower().split()
        counted = {k for k, b in self.tag_checks.items() if b.isChecked()}

        def lacks(path: str) -> bool:
            return bool(counted.intersection(missing_tags(self.db.tags[path])))

        total = sum(1 for path in self.db.tags if lacks(path))
        self.missing_count.setText(
            f"{total} of {len(self.db.tags)} files missing a selected tag"
        )

        def wanted(path: str) -> bool:
            if only and not lacks(path):
                return False
            if needles:
                hay = self._haystacks.get(path)
                if hay is None:
                    hay = self._haystacks[path] = "\n".join(
                        [path]
                        + [
                            v
                            for values in self.db.tags[path].values()
                            for v in values
                        ]
                    ).lower()
                return all(n in hay for n in needles)
            return True

        def visit(parent: QStandardItem) -> bool:
            # Hide rows below parent as needed; True if any row remains visible.
            index = (
                parent.index()
                if parent is not self.model.invisibleRootItem()
                else self.tree.rootIndex()
            )
            any_visible = False
            for row in range(parent.rowCount()):
                child = parent.child(row)
                path = child.data(PATH_ROLE)
                shown = wanted(path) if path is not None else visit(child)
                self.tree.setRowHidden(row, index, not shown)
                any_visible = any_visible or shown
            return any_visible

        self.tree.setUpdatesEnabled(False)
        visit(self.model.invisibleRootItem())
        self.tree.setUpdatesEnabled(True)
        if (
            not self._quiet and self._selected_files() != self.current
        ):  # the filter changed which of the selected files are shown
            self._selection_changed()

    # -- selection ---------------------------------------------------------

    def _files_under(self, item: QStandardItem, seen: set, out: list) -> None:
        path = item.data(PATH_ROLE)
        if path is not None:
            if path not in seen:
                seen.add(path)
                out.append(path)
            return
        for row in range(item.rowCount()):
            if not self.tree.isRowHidden(
                row,
                item.index(),
                # rows the search or the missing-tag filter hides are not
                # selected
                # with their folder
            ):
                self._files_under(item.child(row), seen, out)

    def _hidden(self, item: QStandardItem) -> bool:
        # The row, or a folder above it, is hidden by the filter.
        root = self.model.invisibleRootItem()
        while item is not None:
            parent = item.parent() or root
            if self.tree.isRowHidden(
                item.row(),
                self.tree.rootIndex() if parent is root else parent.index(),
            ):
                return True
            item = item.parent()
        return False

    def _selected_files(self) -> list[str]:
        seen: set = set()
        out: list = []
        for index in self.tree.selectionModel().selectedRows(0):
            item = self.model.itemFromIndex(index)
            if not self._hidden(item):
                self._files_under(item, seen, out)
        return out

    def _selection_changed(self, *_) -> None:
        if self._quiet:
            return
        if not self._confirm_discard():
            self._quiet = True
            self.tree.selectionModel().select(
                self._last_selection,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self._quiet = False
            return
        self._last_selection = QItemSelection(
            self.tree.selectionModel().selection()
        )
        self.current = self._selected_files()
        self.editor.set_files(
            [self.db.tags[p] for p in self.current], self._root, self.current
        )
        self._update_summary()

    def _rate(self, index, rating) -> None:
        # A rating cell was clicked: rate that file.  Ratings are library data,
        # not tags: they are stored at once,
        # whatever tag edits are pending.
        path = index.siblingAtColumn(0).data(PATH_ROLE)
        if path is None:  # a folder row
            return
        paths = [path]
        try:
            self.db.set_ratings(paths, rating)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return
        for p in paths:
            self._fill_cells(p)

    def _update_summary(self) -> None:
        n = len(self.current)
        self.summary.setText(
            "No files selected"
            if n == 0
            else f"Editing {n} file{'s' if n != 1 else ''}"
            + (f": {os.path.basename(self.current[0])}" if n == 1 else "")
        )

    # -- duplicates --------------------------------------------------------

    def _make_duplicates_page(self) -> QWidget:
        self.dups_stale = True
        self.dup_tree = QTreeWidget()
        self.dup_tree.setHeaderLabels(
            ["File", "Role", "Format", "Size", "Modified", "Album"]
        )
        self.dup_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.dup_tree.setUniformRowHeights(True)
        self.dup_tree.setColumnWidth(0, 420)
        self.dup_tree.itemSelectionChanged.connect(self._dups_selected)
        self.dup_filter = QLineEdit()
        self.dup_filter.setPlaceholderText(
            "Filter groups: every word must match a path or tag value of a file"
            " in the group..."
        )
        self.dup_filter.setClearButtonEnabled(True)
        self.dup_timer = QTimer(self)
        self.dup_timer.setSingleShot(True)
        self.dup_timer.setInterval(300)
        self.dup_timer.timeout.connect(
            lambda: (
                setattr(self, "dups_stale", True),
                self._refresh_duplicates(),
            )
        )
        self.dup_filter.textChanged.connect(lambda _: self.dup_timer.start())
        self.dup_summary = QLabel()
        self.dup_make_primary = QPushButton("Make primary")
        self.dup_make_primary.setToolTip(
            "Make the selected duplicate the primary; the old primary and the"
            " others become its duplicates"
        )
        self.dup_make_primary.clicked.connect(self._make_primary)
        self.dup_undo = QPushButton("Undo duplicate mark")
        self.dup_undo.setToolTip(
            "Clear the duplicate mark of the selected duplicates.\nOn a"
            " primary, clears the marks of all its duplicates (the group"
            " disappears)."
        )
        self.dup_undo.clicked.connect(self._undo_duplicate)
        buttons = QHBoxLayout()
        buttons.addWidget(self.dup_summary)
        buttons.addStretch()
        buttons.addWidget(self.dup_make_primary)
        buttons.addWidget(self.dup_undo)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.dup_filter)
        layout.addWidget(self.dup_tree)
        layout.addLayout(buttons)
        self._update_dup_buttons()
        return page

    def _refresh_duplicates(self) -> None:
        # Rebuild the Duplicates tab: one top-level row per primary, its copies
        # below.
        if self.tabs.currentIndex() != 2 or not self.dups_stale:
            return
        self.dups_stale = False
        needles = self.dup_filter.text().lower().split()
        info = {
            path: (size, mtime)
            for path, size, mtime in self.db.con.execute(
                "SELECT path, size, mtime FROM media_file"
            )
        }
        first = lambda path, key: (self.db.tags[path].get(key) or [""])[0]

        def haystack(path: str) -> str:
            return "\n".join(
                [path]
                + [v for values in self.db.tags[path].values() for v in values]
            ).lower()

        def row(path: str, role: str) -> QTreeWidgetItem:
            size, mtime = info.get(path, (None, None))
            item = QTreeWidgetItem([
                os.path.basename(path),
                role,
                os.path.splitext(path)[1].lstrip(".").lower(),
                f"{size / 1e6:.1f} MB" if size else "",
                (
                    time.strftime("%Y-%m-%d", time.localtime(mtime))
                    if mtime
                    else ""
                ),
                first(path, "ALBUM"),
            ])
            item.setData(0, PATH_ROLE, [path])
            item.setToolTip(0, path)
            return item

        selected = {
            p
            for item in self.dup_tree.selectedItems()
            for p in item.data(0, PATH_ROLE)
        }
        self.dup_tree.blockSignals(True)
        self.dup_tree.clear()
        groups = 0
        for primary in sorted(
            self.db.copies,
            key=lambda p: (
                first(p, "ARTIST").lower(),
                first(p, "TITLE").lower(),
                p,
            ),
        ):
            members = [primary] + sorted(self.db.copies[primary])
            if needles and not all(
                any(n in haystack(m) for m in members) for n in needles
            ):
                continue
            top = row(primary, "Primary")
            title = (
                first(primary, "TITLE")
                or os.path.splitext(os.path.basename(primary))[0]
            )
            top.setText(
                0,
                f"{title}  \u2014  {first(primary, 'ARTIST')}".rstrip(
                    " \u2014 "
                )
                + f"   [{os.path.basename(primary)}]",
            )
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            for copy in members[1:]:
                top.addChild(row(copy, "Duplicate"))
            self.dup_tree.addTopLevelItem(top)
            groups += 1
        for item in self._all_dup_items():
            if item.data(0, PATH_ROLE)[0] in selected:
                item.setSelected(True)
        self.dup_tree.blockSignals(False)
        n = sum(len(v) for v in self.db.copies.values())
        self.dup_summary.setText(
            f"{groups} groups shown; {len(self.db.copies)} groups, {n}"
            " duplicate files in total"
            if self.db.has_duplicates
            else "This database has no duplicate_of column"
        )
        self._update_dup_buttons()

    def _all_dup_items(self):
        for i in range(self.dup_tree.topLevelItemCount()):
            top = self.dup_tree.topLevelItem(i)
            yield top
            for j in range(top.childCount()):
                yield top.child(j)

    def _dup_selection(self) -> list[str]:
        return list(
            dict.fromkeys(
                p
                for item in self.dup_tree.selectedItems()
                for p in item.data(0, PATH_ROLE)
            )
        )

    def _update_dup_buttons(self) -> None:
        paths = self._dup_selection()
        self.dup_undo.setEnabled(bool(paths))
        self.dup_make_primary.setEnabled(
            len(paths) == 1 and paths[0] in self.db.dup_of
        )

    def _dups_selected(self) -> None:
        # Select, in the Files tab, the files chosen here (so their tags can be
        # edited).
        self._update_dup_buttons()
        selection = QItemSelection()
        for path in self._dup_selection():
            index = self.cells[path][0].index()
            selection.select(index, index)
        self.tree.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    def _duplicates_changed(self, paths: set[str]) -> None:
        for path in paths:
            self._fill_cells(path)
        self._apply_missing_filter()
        self.dups_stale = True
        self._refresh_duplicates()

    def _undo_duplicate(self) -> None:
        marks: dict[str, str | None] = {}
        for path in self._dup_selection():
            if path in self.db.dup_of:
                marks[path] = None
            for copy in self.db.copies.get(
                path, ()
            ):  # a primary: dissolve its group
                marks[copy] = None
        if not marks:
            return
        if (
            QMessageBox.question(
                self,
                "Undo duplicate mark",
                f"Clear the duplicate mark of {len(marks)} file(s)?\nThe files"
                " themselves are not touched.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        affected = set(marks) | {
            self.db.dup_of[p] for p in marks if p in self.db.dup_of
        }
        try:
            self.db.set_duplicates(marks)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return
        self._duplicates_changed(affected)

    def _make_primary(self) -> None:
        paths = self._dup_selection()
        if len(paths) != 1 or paths[0] not in self.db.dup_of:
            return
        new = paths[0]
        old = self.db.dup_of[new]
        members = [old] + [c for c in self.db.copies.get(old, ()) if c != new]
        marks: dict[str, str | None] = {new: None}
        marks.update({m: new for m in members})
        try:
            self.db.set_duplicates(marks)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return
        self._duplicates_changed(set(marks))

    # -- tag overview ------------------------------------------------------

    def _refresh_overview(self) -> None:
        # Rebuild the Tags tab: every tag name and its values.
        if self.tabs.currentIndex() != 1:
            return
        needle = self.overview_filter.text().strip().lower()
        shown = (
            needle,
            self.variants_only.isChecked(),
            self.lists_only.isChecked(),
        )
        if not self.overview_stale and shown == getattr(
            self, "_shown_filter", None
        ):
            return
        self._shown_filter = shown
        if self.overview_stale:
            index: dict[str, dict[str, list[str]]] = {}
            for path, tags in self.db.tags.items():
                for key, values in tags.items():
                    by_value = index.setdefault(key, {})
                    for value in set(values):
                        by_value.setdefault(value, []).append(path)
            self.overview_index = index
            self._variant_cache = {}
            self.overview_stale = False
        expanded: set[tuple] = set()
        selected: set[tuple] = set()
        self._remember(
            self.overview.invisibleRootItem(), (), expanded, selected
        )
        scroll = self.overview.verticalScrollBar().value()
        self.overview.blockSignals(True)
        self.overview.clear()
        for key in sorted(self.overview_index, key=tagio.sort_key):
            values = self._visible_values(key, needle)
            if not values:
                continue
            paths = {p for ps in values.values() for p in ps}
            groups = len(set(self._variants(key).values()))
            item = QTreeWidgetItem(
                [key, str(len(paths)), f"{groups} to unify" if groups else ""]
            )
            item.setData(0, PATH_ROLE, sorted(paths))
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
            )
            self.overview.addTopLevelItem(item)
            if needle and needle not in key.lower():
                expanded.add((key,))  # matched on a value: show which
        self.overview.blockSignals(False)
        # Put the tree back as it was: expanded nodes open, surviving selected
        # nodes selected.
        # (Selecting here must not re-select files in the Files tab, which keeps
        # its own selection.)
        for i in range(self.overview.topLevelItemCount()):
            self._restore(self.overview.topLevelItem(i), (), expanded, selected)
        self.overview.verticalScrollBar().setValue(scroll)

    def _remember(
        self, item: QTreeWidgetItem, path: tuple, expanded: set, selected: set
    ) -> None:
        # Record which nodes below item are expanded or selected, as paths of
        # node texts.
        for i in range(item.childCount()):
            child = item.child(i)
            here = path + (child.text(0),)
            if child.isExpanded():
                expanded.add(here)
            if child.isSelected():
                selected.add(here)
            self._remember(child, here, expanded, selected)

    def _restore(
        self, item: QTreeWidgetItem, path: tuple, expanded: set, selected: set
    ) -> None:
        here = path + (item.text(0),)
        self.overview.blockSignals(True)
        if here in selected:
            item.setSelected(True)
        self.overview.blockSignals(False)
        if here in expanded:
            item.setExpanded(True)  # builds the children
            for i in range(item.childCount()):
                self._restore(item.child(i), here, expanded, selected)

    def _variants(self, key: str) -> dict[str, int]:
        # Values of a tag that are different spellings of the same name -> a
        # group number.
        if key not in self._variant_cache:
            if key in NUMERIC_TAGS or key.startswith(("REPLAYGAIN", "ID3:")):
                self._variant_cache[key] = (
                    {}
                )  # numbers and flags: "1/12" is not a spelling of "112"
                return {}
            names: dict[str, list[str]] = {}
            for value in self.overview_index.get(key, {}):
                names.setdefault(normalize(value), []).append(value)
            same = sorted(
                (n, vs) for n, vs in names.items() if n and len(vs) > 1
            )
            self._variant_cache[key] = {
                v: i for i, (_n, vs) in enumerate(same) for v in vs
            }
        return self._variant_cache[key]

    def _visible_values(self, key: str, needle: str) -> dict[str, list[str]]:
        values = self.overview_index.get(key, {})
        if needle and needle not in key.lower():
            values = {v: ps for v, ps in values.items() if needle in v.lower()}
        if self.lists_only.isChecked():
            values = {
                v: ps
                for v, ps in values.items()
                if key in SPLIT_TAGS
                and any(sep in v for sep in LIST_SEPARATORS)
            }
        if self.variants_only.isChecked():
            variants = self._variants(key)
            values = {v: ps for v, ps in values.items() if v in variants}
        return values

    def _overview_expanded(self, item: QTreeWidgetItem) -> None:
        # Tag -> its values; value -> the albums (folder + ALBUM tag) that have
        # it.
        if item.childCount():
            return
        self.overview.blockSignals(True)
        key = item.data(0, KEY_ROLE)
        if key is None:
            needle = self.overview_filter.text().strip().lower()
            variants = self._variants(item.text(0))
            for value, paths in sorted(
                self._visible_values(item.text(0), needle).items(),
                key=lambda kv: (normalize(kv[0]), kv[0].lower()),
            ):
                child = QTreeWidgetItem([value_label(value), str(len(paths))])
                if value in variants:
                    group = variants[value]
                    child.setBackground(
                        0,
                        QBrush(
                            QColor.fromHsv(
                                VARIANT_HUES[group % len(VARIANT_HUES)],
                                110,
                                255,
                                90,
                            )
                        ),
                    )
                    child.setToolTip(
                        0,
                        "Same name, different spelling:\n"
                        + "\n".join(
                            v for v, g in variants.items() if g == group
                        ),
                    )
                child.setData(0, PATH_ROLE, sorted(paths))
                child.setData(0, KEY_ROLE, item.text(0))
                child.setData(0, VALUE_ROLE, value)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsEditable)
                child.setChildIndicatorPolicy(
                    QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
                )
                item.addChild(child)
        elif item.data(0, ALBUM_ROLE) is None:
            albums: dict[tuple[str, str], list[str]] = {}
            for path in item.data(0, PATH_ROLE):
                album = (self.db.tags[path].get("ALBUM") or [""])[0]
                albums.setdefault((os.path.dirname(path), album), []).append(
                    path
                )
            for (folder, album), paths in sorted(
                albums.items(), key=lambda kv: (kv[0][1].lower(), kv[0][0])
            ):
                child = QTreeWidgetItem(
                    [f"{album or '<no album>'}    [{folder}]", str(len(paths))]
                )
                child.setData(0, PATH_ROLE, sorted(paths))
                child.setData(0, ALBUM_ROLE, True)
                child.setToolTip(0, folder)
                item.addChild(child)
        self.overview.blockSignals(False)

    def _overview_edited(self, item: QTreeWidgetItem, column: int) -> None:
        # A value was renamed in the Tags tab: change it in every file that has
        # it.
        key, old = item.data(0, KEY_ROLE), item.data(0, VALUE_ROLE)
        if column != 0 or key is None or item.data(0, ALBUM_ROLE):
            return
        new = item.text(0).strip()
        if new == value_label(old):
            return
        paths = list(item.data(0, PATH_ROLE))
        if new == old:
            return
        # In a tag that takes several values, "A; B" splits the value into two
        # values.
        replacement = (
            [v.strip() for v in new.split(";") if v.strip()]
            if key in SPLIT_TAGS or self._suggest(key)
            else [new]
        ) or [new]

        if (
            not new
            or not self._confirm_discard()
            or QMessageBox.question(
                self,
                "Change value",
                f"Change {key} '{old}' to"
                f" {' + '.join(repr(v) for v in replacement)} in {len(paths)}"
                " file(s)?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            self.overview.blockSignals(True)
            item.setText(0, value_label(old))
            self.overview.blockSignals(False)
            return

        def changes(path: str) -> dict:
            values = []
            for v in self.db.tags[path][key]:
                values.extend(replacement if v == old else [v])
            return {
                key: list(dict.fromkeys(values))
            }  # merging into an existing value drops duplicates

        self._write(paths, changes)
        self.editor.set_files(
            [self.db.tags[p] for p in self.current], self._root, self.current
        )
        self._refresh_overview()

    def _overview_selected(self) -> None:
        # Select, in the file tree, the files that have the chosen tag/values.
        wanted: set[str] = set()
        for item in self.overview.selectedItems():
            wanted.update(item.data(0, PATH_ROLE) or [])
        selection = QItemSelection()
        for path in wanted:
            index = self.cells[path][0].index()
            selection.select(index, index)
        self.tree.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    def _bulk_value(self, mode: str, key: str) -> None:
        # Add one value to a tag in every selected file, or remove one, leaving
        # each file's other values and tags alone.
        # It is applied at once (after asking), like a rename in the Tags tab.
        paths = list(self.current)
        if not paths or not self._confirm_discard():
            return
        paths = list(self.current)
        if mode == "add":
            choices = self._suggest(key)
            if choices:
                text, ok = QInputDialog.getItem(
                    self,
                    f"Add a value to {key}",
                    f"Value to add to {key} in {len(paths)} file(s):",
                    choices,
                    0,
                    True,
                )
                text = text if ok else ""
            else:
                text, ok = QInputDialog.getText(
                    self,
                    f"Add a value to {key}",
                    f"Value to add to {key} in {len(paths)} file(s):",
                )
            values = (
                (
                    [v.strip() for v in text.split(";") if v.strip()]
                    if key in SPLIT_TAGS or choices
                    else [text.strip()]
                )
                if ok
                else []
            )
            values = [v for v in dict.fromkeys(values) if v]
            if not values:
                return
            affected = [
                p
                for p in paths
                if any(v not in self.db.tags[p].get(key, []) for v in values)
            ]
            question = (
                f"Add {' + '.join(repr(v) for v in values)} to {key} in"
                f" {len(affected)} of {len(paths)} file(s)?"
            )

            def changes(path: str) -> dict:
                current = list(self.db.tags[path].get(key, []))
                return {key: current + [v for v in values if v not in current]}

        else:
            counts: dict[str, int] = {}
            for p in paths:
                for v in set(self.db.tags[p].get(key, [])):
                    counts[v] = counts.get(v, 0) + 1
            if not counts:
                QMessageBox.information(
                    self,
                    "Remove a value",
                    f"None of the selected files has {key}.",
                )
                return
            labels = [
                f"{v}   ({n} file{'s' if n != 1 else ''})"
                for v, n in sorted(counts.items(), key=lambda kv: kv[0].lower())
            ]
            label, ok = QInputDialog.getItem(
                self,
                f"Remove a value from {key}",
                f"Value to remove from {key}:",
                labels,
                0,
                False,
            )
            if not ok:
                return
            value = sorted(counts, key=str.lower)[labels.index(label)]
            affected = [
                p for p in paths if value in self.db.tags[p].get(key, [])
            ]
            question = (
                f"Remove {value!r} from {key} in {len(affected)} of"
                f" {len(paths)} file(s)?"
            )

            def changes(path: str) -> dict:
                return {
                    key: [
                        v for v in self.db.tags[path].get(key, []) if v != value
                    ]
                }

        if not affected:
            QMessageBox.information(
                self,
                "Nothing to change",
                "Every selected file is already as asked.",
            )
            return
        if (
            QMessageBox.question(self, "Change tags", question)
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._write(affected, changes)
        self.editor.set_files(
            [self.db.tags[p] for p in self.current], self._root, self.current
        )
        self._refresh_overview()

    # -- saving ------------------------------------------------------------

    def _editor_changed(self) -> None:
        dirty = bool(self.editor.pending_changes())
        self.save_button.setEnabled(dirty)
        self.save_action.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)
        self.setWindowTitle(
            f"Tag Database Editor - {os.path.basename(self.db.path)}"
            + (" *" if dirty else "")
        )

    def _write(self, paths: list[str], changes_for) -> bool:
        try:
            self.db.write(paths, changes_for)
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Cannot save", str(e))
            return False
        for path in paths:
            self._fill_cells(path)
        self._apply_missing_filter()
        self.overview_stale = True
        return True

    def save(self) -> bool:
        changes = self.editor.pending_changes()
        if not changes:
            return True
        paths = list(self.current)
        from_overview = bool(self.overview.selectedItems())
        ok = self._write(paths, lambda _path: changes)
        self.editor.set_files(
            [self.db.tags[p] for p in paths], self._root, paths
        )
        self._refresh_overview()
        if ok and from_overview and self.tabs.currentIndex() == 1:
            for key, values in changes.items():
                if values and len(values) == 1:
                    self._reveal(key, values[0], set(paths))
        return ok

    def _reveal(self, key: str, value: str, paths: set[str]) -> None:
        # Open the Tags tab at key/value and select the albums holding these
        # files, so the
        # selection follows the files to their new value.
        top = next(
            (
                self.overview.topLevelItem(i)
                for i in range(self.overview.topLevelItemCount())
                if self.overview.topLevelItem(i).text(0) == key
            ),
            None,
        )
        if top is None:
            return
        top.setExpanded(True)
        node = next(
            (
                top.child(i)
                for i in range(top.childCount())
                if top.child(i).data(0, VALUE_ROLE) == value
            ),
            None,
        )
        if node is None:
            return
        node.setExpanded(True)
        self.overview.blockSignals(True)
        first = None
        for i in range(node.childCount()):
            album = node.child(i)
            if paths.intersection(album.data(0, PATH_ROLE)):
                album.setSelected(True)
                first = first or album
        self.overview.blockSignals(False)
        if first is not None:
            self.overview.scrollToItem(first)

    def _confirm_discard(self) -> bool:
        # True if there is nothing unsaved, or the user saved or discarded it.
        if not self.editor.pending_changes():
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save the changes to the selected files?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _restore_layout(self) -> None:
        settings = QSettings("file-tags", "tagdb_editor")
        geometry, splitter = settings.value("geometry"), settings.value(
            "splitter"
        )
        if geometry is not None:
            self.restoreGeometry(geometry)
        if splitter is not None:
            self.splitter.restoreState(splitter)

    def _save_layout(self) -> None:
        settings = QSettings("file-tags", "tagdb_editor")
        settings.setValue(
            "geometry", self.saveGeometry()
        )  # size, position, maximized
        settings.setValue("splitter", self.splitter.saveState())
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
        self.db.con.close()
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Edit the tags stored in a SQLite tag database (created by"
            " initialize_database.py)."
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
