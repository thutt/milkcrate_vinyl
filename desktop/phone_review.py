# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Lists the songs whose plays and ratings came from the phone in a sync, so
# that they can be rated or corrected at the desktop; a song played while
# driving can be rated later.
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from rating_delegate import (
    RATING_ROLE,
    STAR_MARGIN,
    STAR_SLOT,
    RatingDelegate,
)
from tagdb import SEPARATOR, stat_text

HEADERS = ["Title", "Artist", "Album", "Rating", "Was", "Plays", "Last played"]
RATING_COLUMN = 3
PATH_ROLE = Qt.ItemDataRole.UserRole + 1


def _text(db, path: str, key: str) -> str:
    return SEPARATOR.join(db.tags[path].get(key, []))


def _was(rating, plays) -> str:
    # what the desktop had before the phone's values replaced it
    stars = "not rated" if rating is None else f"{rating} ★"
    return f"{stars}, {plays or 0} plays"


class ReviewDelegate(RatingDelegate):
    # The stars, and the words "not rated" where there are none.
    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        rating = index.data(RATING_ROLE)
        if rating is not None and rating >= 0:
            return
        painter.save()
        font = painter.font()
        font.setItalic(True)
        painter.setFont(font)
        painter.setOpacity(0.6)
        painter.drawText(
            option.rect.adjusted(STAR_MARGIN, 0, 0, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "not rated",
        )
        painter.restore()


class PhoneReview(QDialog):
    # changed(paths) is called after songs were rated here.
    def __init__(self, parent, db, report, changed):
        super().__init__(parent)
        self.db, self.changed = db, changed
        self.setWindowTitle("Changes from the phone")
        self.resize(1000, 560)
        before: dict = {}
        for path, rating, plays, last in report.changes:
            before.setdefault(path, (rating, plays))  # the first round's
        self.paths = [p for p in before if p in db.tags]
        self.before = before
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.unrated = QCheckBox("Only songs that are not rated")
        self.unrated.toggled.connect(self._filter)
        layout.addWidget(self.unrated)
        self.table = QTableWidget(len(self.paths), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setItemDelegateForColumn(
            RATING_COLUMN, ReviewDelegate(self._rate, self.table)
        )
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.table, 1)
        for number in range(6):  # 0 to 5 stars on the selected rows
            QShortcut(
                QKeySequence(str(number)),
                self.table,
                activated=lambda n=number: self._rate_selected(n),
            ).setContext(Qt.ShortcutContext.WidgetShortcut)
        QShortcut(
            QKeySequence(Qt.Key.Key_Delete),
            self.table,
            activated=lambda: self._rate_selected(None),
        ).setContext(Qt.ShortcutContext.WidgetShortcut)
        layout.addWidget(
            QLabel(
                "Click the stars, or select songs and press 0 to 5 "
                "(Delete: not rated).  Changes are stored at once and "
                "go to the phone at the next sync."
            )
        )
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.table.setSortingEnabled(False)
        for r, path in enumerate(self.paths):
            self._fill(r, path)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, 260)
        words = self.table.fontMetrics().horizontalAdvance("not rated")
        self.table.setColumnWidth(
            RATING_COLUMN, 2 * STAR_MARGIN + max(5 * STAR_SLOT, words) + 12
        )
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(
            RATING_COLUMN, Qt.SortOrder.AscendingOrder
        )  # not rated first
        self._filter()

    def _row_of(self, path: str) -> int:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(PATH_ROLE) == path:
                return r
        return -1

    def _fill(self, r: int, path: str) -> None:
        stats = self.db.stats.get(path, (None, 0, None))
        rating = stats[0]
        was_rating, was_plays = self.before[path]
        cells = [
            _text(self.db, path, "TITLE") or path,
            _text(self.db, path, "ARTIST"),
            _text(self.db, path, "ALBUM"),
            "",
            _was(was_rating, was_plays),
            stat_text(stats, "@plays"),
            stat_text(stats, "@last"),
        ]
        for c, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setData(PATH_ROLE, path)
            if c == RATING_COLUMN:
                # the number is what the column sorts by; the delegate draws
                # the stars instead of it
                value = -1 if rating is None else rating
                item.setData(RATING_ROLE, value)
                item.setData(Qt.ItemDataRole.DisplayRole, value)
            self.table.setItem(r, c, item)

    def _rate(self, index, rating) -> None:
        path = self.table.item(index.row(), 0).data(PATH_ROLE)
        self._set([path], rating)

    def _rate_selected(self, rating) -> None:
        paths = {
            self.table.item(i.row(), 0).data(PATH_ROLE)
            for i in self.table.selectionModel().selectedRows()
        }
        if paths:
            self._set(sorted(paths), rating)

    def _set(self, paths: list[str], rating) -> None:
        self.db.set_ratings(paths, rating)
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        for path in paths:
            r = self._row_of(path)
            if r >= 0:
                value = -1 if rating is None else rating
                item = self.table.item(r, RATING_COLUMN)
                item.setData(RATING_ROLE, value)
                item.setData(Qt.ItemDataRole.DisplayRole, value)
        self.table.setSortingEnabled(sorting)
        self.table.viewport().update()
        self.changed(paths)
        self._filter(refresh_only=not self.unrated.isChecked())

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        rate = menu.addMenu("Rate")
        rate.addAction("Not rated", lambda: self._rate_selected(None))
        for n in range(6):
            rate.addAction(
                f"{n} star{'s' if n != 1 else ''}",
                lambda n=n: self._rate_selected(n),
            )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _filter(self, *_, refresh_only: bool = False) -> None:
        unrated = 0
        for r in range(self.table.rowCount()):
            path = self.table.item(r, 0).data(PATH_ROLE)
            is_unrated = self.db.stats.get(path, (None,))[0] is None
            unrated += is_unrated
            if not refresh_only:
                self.table.setRowHidden(
                    r, self.unrated.isChecked() and not is_unrated
                )
        self.summary.setText(
            f"{len(self.paths)} song(s) took plays or a rating from the phone;"
            f" {unrated} not rated."
        )


def review_phone_changes(parent, db, report, changed) -> None:
    # Offer the songs the phone changed for rating.  Nothing opens when the
    # phone changed none.
    if not report or not report.changes:
        return
    PhoneReview(parent, db, report, changed).exec()
