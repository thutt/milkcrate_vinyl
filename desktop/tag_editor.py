# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The tag editing panel shared by the database editor and the player.
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QFont, QKeySequence, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import tagio
from path_display import pathname_html

PATH_ROLE = Qt.ItemDataRole.UserRole + 1
MIXED_ROLE = Qt.ItemDataRole.UserRole + 2
KEY_ROLE = Qt.ItemDataRole.UserRole + 3
VALUE_ROLE = Qt.ItemDataRole.UserRole + 4
MIXED_TEXT = "<multiple values>"
SEPARATOR = "; "

COL_KEY, COL_VALUE, COL_FILES = range(3)


# --------------------------------------------------------------------- editor


@dataclass
class Row:
    key: str
    orig: (
        list[str] | None
    )  # common value of all files that have the tag; None = they differ
    present: int  # how many of the selected files have the tag
    multi: bool  # some file has several values for it
    new: bool = False
    values: list[str] | None = None  # the user's replacement value, if any
    deleted: bool = False

    @property
    def mixed(self) -> bool:
        return self.orig is None and self.values is None

    @property
    def edited(self) -> bool:
        return self.values is not None


class _Delegate(QStyledItemDelegate):
    # Line editors; tag names get completion, mixed values get a placeholder.
    #
    # If the owning TagEditor has a `suggest` function and it returns values for
    # the tag, the value
    # is edited in a combo box: pick one of those values or type a new one.

    def __init__(self, parent, owner=None):
        super().__init__(parent)
        self.owner = owner

    def _choices(self, index) -> list[str]:
        suggest = getattr(self.owner, "suggest", None)
        if index.column() != COL_VALUE or suggest is None:
            return []
        return suggest(self.owner.rows[index.row()].key) or []

    def createEditor(self, parent, option, index):
        choices = self._choices(index)
        if choices:
            combo = QComboBox(parent)
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.addItems(choices)
            combo.activated.connect(
                lambda _: (
                    self.commitData.emit(combo),
                    self.closeEditor.emit(combo),
                )
            )
            return combo
        editor = QLineEdit(parent)
        if index.column() == COL_KEY:
            completer = QCompleter(tagio.COMMON_KEYS, editor)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            editor.setCompleter(completer)
        return editor

    def setEditorData(self, editor, index):
        line = editor.lineEdit() if isinstance(editor, QComboBox) else editor
        if index.data(MIXED_ROLE):
            line.setText("")
            line.setPlaceholderText(
                MIXED_TEXT + " - type to replace in all files"
            )
        else:
            line.setText(index.data(Qt.ItemDataRole.EditRole) or "")
            if line is not editor:
                line.selectAll()

    def setModelData(self, editor, model, index):
        text = (
            editor.currentText()
            if isinstance(editor, QComboBox)
            else editor.text()
        )
        if index.data(MIXED_ROLE) and not text:
            return  # left untouched
        model.setData(index, text)


class TagEditor(QWidget):
    # Edits the tags of several files at once.
    #
    # Tags the files agree on show their value; tags where they differ show
    # "<multiple values>" and are only changed if the user types something.
    # Nothing is written until the owner asks for pending_changes().

    changed = pyqtSignal()
    bulk_requested = pyqtSignal(
        str,
        str,
        # "add" or "remove", tag name: add/remove one value in every selected
        # file
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []
        self._total = 0
        self._busy = False
        self.split_keys: frozenset[str] = (
            frozenset()
        )  # tags where ';' always separates several values
        self.suggest = (
            None  # optional: tag name -> values to offer when editing that tag
        )

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Tag", "Value", "Files"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setItemDelegate(_Delegate(self.table, self))
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemDelegate().closeEditor.connect(
            lambda *_: QTimer.singleShot(0, self._prune_blank)
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_KEY, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            COL_FILES, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setColumnWidth(COL_KEY, 150)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        self.add_button = QPushButton("Add Tag")
        self.add_button.clicked.connect(self.add_tag)
        self.remove_button = QPushButton("Remove Tag")
        self.remove_button.setToolTip(
            "Remove the selected tags from all selected files (Delete)"
        )
        self.remove_button.clicked.connect(self.remove_selected)
        self.add_value_button = QPushButton("Add Value...")
        self.add_value_button.setToolTip(
            "Add one value to the selected tag in every selected file, keeping"
            " the values they have"
        )
        self.add_value_button.clicked.connect(lambda: self._bulk("add"))
        self.remove_value_button = QPushButton("Remove Value...")
        self.remove_value_button.setToolTip(
            "Remove one value from the selected tag in every selected file that"
            " has it, keeping the others"
        )
        self.remove_value_button.clicked.connect(lambda: self._bulk("remove"))
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.add_value_button)
        buttons.addWidget(self.remove_value_button)
        buttons.addStretch()

        # <pathname>: a pseudo-tag, the full path of the selected file, with
        # the music root dimmed; it is not stored in the file or the database
        self.path_key = QLabel("<pathname>")
        self.path_key.setEnabled(False)  # dimmed, like a name that is not a tag
        self.path_value = QLabel()
        self.path_value.setTextFormat(Qt.TextFormat.RichText)
        self.path_value.setWordWrap(True)
        self.path_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.path_row = QWidget()
        path_layout = QHBoxLayout(self.path_row)
        path_layout.setContentsMargins(4, 0, 4, 0)
        path_layout.addWidget(self.path_key, 0, Qt.AlignmentFlag.AlignTop)
        path_layout.addWidget(self.path_value, 1)
        self.path_row.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.path_row)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

        delete = QAction(self.table)
        delete.setShortcut(QKeySequence.StandardKey.Delete)
        delete.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        delete.triggered.connect(self.remove_selected)
        self.table.addAction(delete)
        self._update_buttons()

    # -- model -------------------------------------------------------------

    def set_files(
        self,
        tag_dicts: list[dict[str, list[str]]],
        root: str = "",
        paths: list[str] | None = None,
    ) -> None:
        # Show the tags of these files, discarding any pending edits.  paths
        # (relative to the music root) are shown as the <pathname> pseudo-tag.
        self._show_pathname(root, paths or [])
        self._total = len(tag_dicts)
        self.rows = []
        keys = sorted({k for d in tag_dicts for k in d}, key=tagio.sort_key)
        for key in keys:
            lists = [d[key] for d in tag_dicts if key in d]
            same = all(v == lists[0] for v in lists)
            self.rows.append(
                Row(
                    key,
                    lists[0] if same else None,
                    len(lists),
                    any(len(v) > 1 for v in lists),
                )
            )
        self._rebuild()
        self.changed.emit()

    def _show_pathname(self, root: str, paths: list[str]) -> None:
        dim = self.palette().color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
        )
        self.path_value.setText(pathname_html(root, paths, dim))
        self.path_value.setToolTip(
            f"Music root: {root}\nThe database stores the part after it."
            if paths
            else ""
        )
        self.path_row.setVisible(bool(paths))

    def revert(self) -> None:
        for row in list(self.rows):
            row.values, row.deleted = None, False
        self.rows = [r for r in self.rows if not r.new]
        self._rebuild()
        self.changed.emit()

    def pending_changes(self) -> dict[str, list[str] | None]:
        changes: dict[str, list[str] | None] = {}
        for row in self.rows:
            if not row.key:
                continue
            if row.deleted:
                if not row.new:
                    changes[row.key] = None
            elif row.edited:
                changes[row.key] = row.values or None
        return changes

    # -- view --------------------------------------------------------------

    def _rebuild(self) -> None:
        self._busy = True
        self.table.setRowCount(len(self.rows))
        for i in range(len(self.rows)):
            for col in range(3):
                self.table.setItem(i, col, QTableWidgetItem())
            self._refresh(i)
        self._busy = False
        self._update_buttons()

    def _refresh(self, i: int) -> None:
        row = self.rows[i]
        key_item, value_item, files_item = (
            self.table.item(i, c) for c in range(3)
        )
        was_busy, self._busy = self._busy, True

        shown = row.values if row.edited else row.orig
        value_item.setText(
            MIXED_TEXT if shown is None else SEPARATOR.join(shown)
        )
        value_item.setData(MIXED_ROLE, row.mixed)
        key_item.setText(row.key)
        if row.present < self._total and not row.new:
            files_item.setText(f"{row.present} of {self._total}")
        else:
            files_item.setText("")
        if row.new and row.key:
            value_item.setToolTip("")

        font = QFont(self.table.font())
        font.setBold(row.edited and not row.deleted)
        font.setStrikeOut(row.deleted)
        font.setItalic(row.mixed)
        dim = QBrush(
            self.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            )
        )
        for item in (key_item, value_item, files_item):
            item.setFont(font)
            item.setForeground(
                dim
                if row.deleted or (row.mixed and item is value_item)
                else QBrush()
            )
        files_item.setFlags(files_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        editable = Qt.ItemFlag.ItemIsEditable
        if row.new and not row.key:
            key_item.setFlags(key_item.flags() | editable)
            value_item.setFlags(value_item.flags() & ~editable)
        else:
            key_item.setFlags(key_item.flags() & ~editable)
            value_item.setFlags(
                (value_item.flags() & ~editable)
                if row.deleted
                else (value_item.flags() | editable)
            )
        value_item.setToolTip(
            "Several values: separate them with ';'"
            if self._splittable(row)
            else ""
        )
        self._busy = was_busy

    def _update_buttons(self) -> None:
        enabled = self._total > 0
        self.add_button.setEnabled(enabled)
        self.remove_button.setEnabled(
            enabled and bool(self.table.selectedItems())
        )
        row = self._bulk_row()
        self.add_value_button.setEnabled(row is not None)
        self.remove_value_button.setEnabled(row is not None and row.present > 0)

    def _bulk_row(self) -> Row | None:
        # The tag row that Add Value / Remove Value act on: the current row, if
        # it is an existing tag.
        i = self.table.currentRow()
        if self._total == 0 or not 0 <= i < len(self.rows):
            return None
        row = self.rows[i]
        return row if row.key and not row.new else None

    def _bulk(self, mode: str) -> None:
        row = self._bulk_row()
        if row is not None:
            self.bulk_requested.emit(mode, row.key)

    def _table_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is not None:
            self.table.setCurrentCell(item.row(), COL_VALUE)
        row = self._bulk_row()
        if row is None:
            return
        menu = QMenu(self)
        menu.addAction(
            f"Add a value to {row.key} in every selected file..."
        ).triggered.connect(lambda: self._bulk("add"))
        remove = menu.addAction(
            f"Remove a value from {row.key} in every selected file..."
        )
        remove.triggered.connect(lambda: self._bulk("remove"))
        remove.setEnabled(row.present > 0)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # -- editing -----------------------------------------------------------

    def add_tag(self) -> None:
        self._prune_blank()
        self.rows.append(Row("", None, 0, False, new=True, values=[]))
        self._busy = True
        i = len(self.rows) - 1
        self.table.insertRow(i)
        for col in range(3):
            self.table.setItem(i, col, QTableWidgetItem())
        self._refresh(i)
        self._busy = False
        self.table.setCurrentCell(i, COL_KEY)
        self.table.editItem(self.table.item(i, COL_KEY))

    def remove_selected(self) -> None:
        selected = sorted(
            {idx.row() for idx in self.table.selectedIndexes()}, reverse=True
        )
        restoring = bool(selected) and all(
            self.rows[i].deleted for i in selected
        )
        for i in selected:
            row = self.rows[i]
            if row.new:
                self._drop_row(i)
            else:
                row.deleted = not restoring
                self._refresh(i)
        self.changed.emit()

    def _drop_row(self, i: int) -> None:
        del self.rows[i]
        self._busy = True
        self.table.removeRow(i)
        self._busy = False

    def _prune_blank(self) -> None:
        # Drop new rows whose name was never filled in.
        editing = self.table.state() == QAbstractItemView.State.EditingState
        for i in reversed(range(len(self.rows))):
            if (
                self.rows[i].new
                and not self.rows[i].key
                and not (editing and i == self.table.currentRow())
            ):
                self._drop_row(i)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._busy:
            return
        i = item.row()
        row = self.rows[i]
        if item.column() == COL_KEY:
            self._name_chosen(i, item.text().strip().upper())
        elif item.column() == COL_VALUE:
            self._value_chosen(i, item.text().strip())
        self.changed.emit()

    def _name_chosen(self, i: int, key: str) -> None:
        row = self.rows[i]
        if not key:
            QTimer.singleShot(0, self._prune_blank)
            return
        if "=" in key or "\n" in key:
            QMessageBox.warning(
                self,
                "Invalid tag name",
                "Tag names cannot contain '=' or line breaks.",
            )
            self._refresh(i)
            self.table.editItem(self.table.item(i, COL_KEY))
            return
        existing = next(
            (j for j, r in enumerate(self.rows) if r.key == key and j != i),
            None,
        )
        if existing is not None:
            self._drop_row(i)
            self.table.setCurrentCell(
                existing if existing < i else existing - 1, COL_VALUE
            )
            return
        row.key = key
        self._refresh(i)
        self.table.setCurrentCell(i, COL_VALUE)
        QTimer.singleShot(
            0, lambda: self.table.editItem(self.table.item(i, COL_VALUE))
        )

    def _splittable(self, row: Row) -> bool:
        # Whether ';' separates several values in this tag: it does if some file
        # already has
        # several, if the host lists it in split_keys, or if it offers a pick
        # list for it.
        return (
            row.multi
            or row.key in self.split_keys
            or bool(self.suggest and row.key and self.suggest(row.key))
        )

    def _value_chosen(self, i: int, text: str) -> None:
        row = self.rows[i]
        if not text and not row.new:
            row.deleted, row.values = True, None  # emptying a tag removes it
        else:
            values = (
                [v.strip() for v in text.split(";") if v.strip()]
                if self._splittable(row)
                else [text]
            )
            values = list(dict.fromkeys(values))
            # Re-typing the common value is only a no-op if every selected file
            # already has it.
            unchanged = (
                values == row.orig
                and not row.new
                and row.present == self._total
            )
            row.values = None if unchanged else values
            row.deleted = False
        self._refresh(i)


# --------------------------------------------------------------------- scanning
