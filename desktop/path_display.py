# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Showing a track's full pathname with the music root set apart from the
# relative path: the database stores the relative path, the root is recorded
# once (meta.root).
from __future__ import annotations

import html
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

ROOT_ROLE = Qt.ItemDataRole.UserRole + 20  # the root, on a pathname cell


def full_path(root: str, relative: str) -> str:
    return root.rstrip("/") + "/" + relative if root else relative


def pathname_html(root: str, paths: list[str], dim: QColor) -> str:
    # The <pathname> pseudo-tag of the selected files: the root dimmed, then
    # the relative path.  Several files show their common folder and how many.
    if not paths:
        return ""
    prefix = html.escape(root.rstrip("/") + "/") if root else ""
    shade = f'<span style="color:{dim.name()}">{prefix}</span>'
    if len(paths) == 1:
        return shade + html.escape(paths[0])
    folder = os.path.commonpath([os.path.dirname(p) or "." for p in paths])
    folder = "" if folder == "." else folder + "/"
    return shade + html.escape(folder) + f"&lt;{len(paths)} files&gt;"


class PathDelegate(QStyledItemDelegate):
    # Draws a full pathname with the root dimmed and the relative path in the
    # normal colour.  A path too long for the column loses the start of the
    # root first, then more of it, never the end of the relative path.

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )
        if not text:
            return
        root = index.data(ROOT_ROLE) or ""
        relative = text[len(root.rstrip("/")) + 1 :] if root else text
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget
        ).adjusted(3, 0, -3, 0)
        elided = opt.fontMetrics.elidedText(
            text, Qt.TextElideMode.ElideLeft, rect.width()
        )
        if elided == text:
            split = len(text) - len(relative)
        else:  # "..." then the end of the text
            split = max(1, len(elided) - len(relative))
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        role = (
            QPalette.ColorRole.HighlightedText
            if selected
            else QPalette.ColorRole.Text
        )
        normal = opt.palette.color(role)
        dim = opt.palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
        )
        if selected:
            dim = QColor(normal)
            dim.setAlphaF(0.6)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(dim)
        painter.drawText(rect, flags, elided[:split])
        painter.setPen(normal)
        rect.setLeft(
            rect.left() + opt.fontMetrics.horizontalAdvance(elided[:split])
        )
        painter.drawText(rect, flags, elided[split:])
        painter.restore()
