# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Star cells for the rating column, shared by the player and the editor.
from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

SORT_ROLE = (
    Qt.ItemDataRole.UserRole + 21
)  # what a click on a column header sorts by (numbers sort as numbers)
RATING_ROLE = (
    Qt.ItemDataRole.UserRole + 20
)  # a rating cell's rating: -1 = not rated, else 0 to 5
STAR_SLOT = 16  # width of one star in a rating cell, px
STAR_MARGIN = 4


class RatingDelegate(QStyledItemDelegate):
    # Draws a rating cell as five stars and lets the stars be clicked.
    #
    # A click on a not-rated cell switches it to zero stars; a click on a star
    # then sets that many
    # stars (a click left of the first star sets zero); a double-click makes it
    # not rated again.
    # rate(index, rating) is called with 0..5 or None; the owner decides which
    # files that applies to.

    def __init__(self, rate, parent=None):
        super().__init__(parent)
        self.rate = rate
        self._after_double_click = False

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )
        rating = index.data(RATING_ROLE)
        if rating is None or rating < 0:
            return
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setPen(
            opt.palette.color(
                QPalette.ColorRole.HighlightedText
                if selected
                else QPalette.ColorRole.Text
            )
        )
        for i in range(5):
            slot = QRect(
                opt.rect.left() + STAR_MARGIN + i * STAR_SLOT,
                opt.rect.top(),
                STAR_SLOT,
                opt.rect.height(),
            )
            painter.drawText(
                slot,
                Qt.AlignmentFlag.AlignCenter,
                "\u2605" if i < rating else "\u2606",
            )
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        kind = event.type()
        if (
            not hasattr(event, "button")
            or event.button() != Qt.MouseButton.LeftButton
        ):
            return False
        if kind == QEvent.Type.MouseButtonPress:
            # Qt does not always deliver the release that ends a double-click
            self._after_double_click = False
            return False
        if kind == QEvent.Type.MouseButtonDblClick:
            # the release that ends the double-click must not rate again
            self._after_double_click = True
            self.rate(index, None)
            return True
        if kind == QEvent.Type.MouseButtonRelease:
            if self._after_double_click:
                self._after_double_click = False
                return True
            current = index.data(RATING_ROLE)
            if current is None or current < 0:
                self.rate(index, 0)
                return True
            x = event.position().x() - option.rect.left() - STAR_MARGIN
            self.rate(index, 0 if x < 0 else min(int(x // STAR_SLOT) + 1, 5))
            return True
        return False
