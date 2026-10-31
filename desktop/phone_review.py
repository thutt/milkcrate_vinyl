# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Lists the songs whose plays and ratings came from the phone in a sync, so
# that they can be rated or corrected at the desktop; a song played while
# driving can be rated later.  Apply stores the ratings given and takes the
# rated songs off the list, leaving the window open for the songs still to
# rate; Finish stores what is left and closes the window.
from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionViewItem,
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

HEADERS = [
    "Title",
    "Artist",
    "Album",
    "Rating",
    "Was",
    "Plays",
    "Last played",
    "Listened",
]
RATING_COLUMN = 3
PATH_ROLE = Qt.ItemDataRole.UserRole + 1


def _text(db, path: str, key: str) -> str:
    return SEPARATOR.join(db.tags[path].get(key, []))


def _was(rating, plays) -> str:
    # what the desktop had before the phone's values replaced it
    stars = "not rated" if rating is None else f"{rating} ★"
    return f"{stars}, {plays or 0} plays"


def _listened(db, path: str) -> str:
    # the time heard in the latest play on the phone; blank = not known
    row = db.con.execute(
        "SELECT listened_ms FROM media_file WHERE path = ?", (path,)
    ).fetchone()
    if not row or row[0] is None:
        return ""
    seconds = round(row[0] / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


class ReviewDelegate(RatingDelegate):
    # A rating cell of the review: a "0" and five stars.  A click on the "0"
    # rates zero stars, a click on a star gives that many, and a click
    # anywhere else changes nothing; a double-click makes the song not rated.
    # A song that is not rated shows the "0" and the stars faint; zero stars
    # shows the "0" in full and the stars empty.
    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )
        rating = index.data(RATING_ROLE)
        unrated = rating is None or rating < 0
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setPen(
            opt.palette.color(
                QPalette.ColorRole.HighlightedText
                if selected
                else QPalette.ColorRole.Text
            )
        )
        for i in range(6):  # slot 0 is the "0"
            slot = QRect(
                opt.rect.left() + STAR_MARGIN + i * STAR_SLOT,
                opt.rect.top(),
                STAR_SLOT,
                opt.rect.height(),
            )
            if i == 0:
                text = "0"
                faint = unrated or rating != 0
            else:
                text = "\u2605" if not unrated and i <= rating else "\u2606"
                faint = unrated
            painter.setOpacity(0.35 if faint else 1.0)
            painter.drawText(slot, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if (
            not hasattr(event, "button")
            or event.button() != Qt.MouseButton.LeftButton
        ):
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            self._after_double_click = False
            return False
        if kind == QEvent.Type.MouseButtonDblClick:
            self._after_double_click = True
            self.rate(index, None)
            return True
        if kind == QEvent.Type.MouseButtonRelease:
            if self._after_double_click:
                self._after_double_click = False
                return True
            x = event.position().x() - option.rect.left() - STAR_MARGIN
            if 0 <= x < 6 * STAR_SLOT:
                self.rate(index, int(x // STAR_SLOT))
            return True
        return False


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
        self.total = len(self.paths)
        self.before = before
        self.pending: dict[str, int | None] = {}  # ratings given, not stored
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
                "(Delete: not rated).  Apply stores the ratings, which go "
                "to the phone at the next sync, and takes the rated songs "
                "off the list; Finish stores the ratings and closes the "
                "window; Cancel closes it and discards the ratings not "
                "applied."
            )
        )
        row = QHBoxLayout()
        row.addStretch()
        apply = QPushButton("Apply")
        apply.setDefault(True)
        apply.setToolTip(
            "Store the ratings given here and take the rated songs off the"
            " list"
        )
        apply.clicked.connect(self.apply)
        finish = QPushButton("Finish")
        finish.setToolTip("Store the ratings given here and close the window")
        finish.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.setToolTip(
            "Close without storing the ratings given here since the last"
            " Apply"
        )
        cancel.clicked.connect(self.reject)
        row.addWidget(apply)
        row.addWidget(finish)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.table.setSortingEnabled(False)
        for r, path in enumerate(self.paths):
            self._fill(r, path)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(
            RATING_COLUMN, 2 * STAR_MARGIN + 6 * STAR_SLOT + 12
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
            _listened(self.db, path),
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
                item.setToolTip("Click the 0 or a star to rate")
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

    def _rating_of(self, path: str):
        if path in self.pending:
            return self.pending[path]
        return self.db.stats.get(path, (None,))[0]

    def _set(self, paths: list[str], rating) -> None:
        # Only the list changes; Apply stores the ratings.
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        for path in paths:
            self.pending[path] = rating
            r = self._row_of(path)
            if r >= 0:
                value = -1 if rating is None else rating
                item = self.table.item(r, RATING_COLUMN)
                item.setData(RATING_ROLE, value)
                item.setData(Qt.ItemDataRole.DisplayRole, value)
        self.table.setSortingEnabled(sorting)
        self.table.viewport().update()

    def _store(self) -> None:
        by_rating: dict = {}
        for path, rating in self.pending.items():
            by_rating.setdefault(rating, []).append(path)
        for rating, paths in by_rating.items():
            self.db.set_ratings(paths, rating)
        if self.pending:
            self.changed(list(self.pending))
        self.pending = {}

    def apply(self) -> None:
        # Store the ratings and take every rated song off the list; the
        # window stays for the songs that are still not rated.
        self._store()
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        for r in reversed(range(self.table.rowCount())):
            path = self.table.item(r, 0).data(PATH_ROLE)
            if self._rating_of(path) is not None:
                self.table.removeRow(r)
                self.paths.remove(path)
        self.table.setSortingEnabled(sorting)
        self._filter()

    def accept(self) -> None:  # Finish
        self._store()
        super().accept()

    def reject(self) -> None:
        if self.pending and (
            QMessageBox.question(
                self,
                "Cancel",
                f"Discard the ratings given to {len(self.pending)} song(s)?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        super().reject()

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

    def _filter(self, *_) -> None:
        unrated = shown = 0
        for r in range(self.table.rowCount()):
            path = self.table.item(r, 0).data(PATH_ROLE)
            is_unrated = self._rating_of(path) is None
            unrated += is_unrated
            hide = self.unrated.isChecked() and not is_unrated
            self.table.setRowHidden(r, hide)
            shown += not hide
        self.summary.setText(
            f"{self.total} song(s) took plays or a rating from the phone;"
            f" {len(self.paths)} still listed, {unrated} of them not rated."
            f"  Showing {shown}."
        )


def review_phone_changes(parent, db, report, changed) -> None:
    # Offer the songs the phone changed for rating.  Nothing opens when the
    # phone changed none.
    if not report or not report.changes:
        return
    PhoneReview(parent, db, report, changed).exec()
