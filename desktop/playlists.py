# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Playlist panel and rules dialogs shared by the player and the editor.
#
# Manual playlists are edited directly; a dynamic playlist is a set of rules
# (DynamicRulesDialog) from which
# its tracks are selected -- see dynamic_playlists.py.
#
# PlaylistPanel lists the playlists and the entries of the chosen one; entries
# are reordered by dragging
# (or Move up / Move down) and removed with Delete.  populate_add_menu() fills a
# context menu with
# "Add to playlist" choices for the files the caller has selected.  Only the
# database is changed.
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import containers
import dynamic_playlists as dyn
from containers import fmt_clock

ID_ROLE = (
    Qt.ItemDataRole.UserRole + 30
)  # a playlist's id, or a playlist entry's id
PATH_ROLE = Qt.ItemDataRole.UserRole + 31


def ask_new_name(parent: QWidget, title: str, text: str = "") -> str | None:
    name, ok = QInputDialog.getText(parent, title, "Playlist name:", text=text)
    name = name.strip()
    return name if ok and name else None


def create_playlist(parent: QWidget, db) -> int | None:
    # Ask for a name and create the playlist; None if cancelled or the name is
    # taken.
    name = ask_new_name(parent, "New playlist")
    if name is None:
        return None
    try:
        return db.create_playlist(name)
    except sqlite3.IntegrityError:
        QMessageBox.warning(
            parent,
            "New playlist",
            f"There is already a playlist named “{name}”.",
        )
        return None


class RuleRow(QWidget):
    # One rule: field, operator, value.

    def __init__(self, rule, remove, parent=None):
        super().__init__(parent)
        self.starter = (
            rule
            is None
            # a blank rule offered by the dialog; replaced if a group is added
            # before it is used
        )
        self.field = QComboBox()
        for key, label in dyn.FIELDS.items():
            self.field.addItem(label, key)
        self.op = QComboBox()
        self.value = QLineEdit()
        self.remove_button = QPushButton("\u2212")
        self.remove_button.setFixedWidth(28)
        self.remove_button.setToolTip("Remove this rule")
        self.remove_button.clicked.connect(lambda: remove(self))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.field, 2)
        layout.addWidget(self.op, 2)
        layout.addWidget(self.value, 3)
        layout.addWidget(self.remove_button)
        self.field.currentIndexChanged.connect(self._field_changed)
        self.op.currentIndexChanged.connect(self._op_changed)
        if rule:
            self.field.setCurrentIndex(
                max(self.field.findData(rule["field"]), 0)
            )
        self._field_changed()
        if rule:
            self.op.setCurrentIndex(max(self.op.findData(rule["op"]), 0))
            self.value.setText(
                "" if rule.get("value") is None else str(rule["value"])
            )
        self._op_changed()
        for signal in (
            self.field.activated,
            self.op.activated,
            self.value.textEdited,
        ):
            signal.connect(
                lambda *_: setattr(self, "starter", False)
            )  # the user has begun to fill it in

    def _field_changed(self) -> None:
        self.op.blockSignals(True)
        self.op.clear()
        for key, label in dyn.OPS[dyn.field_kind(self.field.currentData())]:
            self.op.addItem(label, key)
        self.op.blockSignals(False)
        self._op_changed()

    def _op_changed(self) -> None:
        needs_value = self.op.currentData() not in dyn.NO_VALUE
        self.value.setEnabled(needs_value)
        if not needs_value:
            self.value.clear()
        self.value.setPlaceholderText(
            ""
            if not needs_value
            else (
                "text"
                if dyn.field_kind(self.field.currentData()) == "text"
                else "whole number"
            )
        )

    def rule(self) -> dict:
        field, op = self.field.currentData(), self.op.currentData()
        if op in dyn.NO_VALUE:
            return {"field": field, "op": op}
        text = self.value.text().strip()
        if dyn.field_kind(field) == "text":
            return {"field": field, "op": op, "value": text}
        try:
            return {"field": field, "op": op, "value": int(text)}
        except ValueError:
            return {
                "field": field,
                "op": op,
                "value": text,
            }  # (rejected by validate with a message)


class GroupWidget(QFrame):
    # A group of rules: "match all/any of these", rule rows, nested groups, Add
    # rule / Add group.

    def __init__(self, group, remove=None, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.remove = remove
        group = group or {"match": "all", "rules": []}
        self.match = QComboBox()
        self.match.addItem("all", "all")
        self.match.addItem("any", "any")
        self.match.setCurrentIndex(0 if group["match"] == "all" else 1)
        self.items: list = []
        self.items_layout = QVBoxLayout()
        add_rule = QPushButton("Add rule")
        add_group = QPushButton("Add group")
        add_group.setToolTip(
            "A group of rules with its own all / any, e.g. (a or b) and c"
        )
        add_rule.clicked.connect(lambda: self.add_rule(None))
        add_group.clicked.connect(lambda: self.add_group(None))
        head = QHBoxLayout()
        head.addWidget(QLabel("Match"))
        head.addWidget(self.match)
        head.addWidget(QLabel("of these:"))
        head.addStretch()
        if remove:
            button = QPushButton("Remove group")
            button.clicked.connect(lambda: remove(self))
            head.addWidget(button)
        foot = QHBoxLayout()
        foot.addWidget(add_rule)
        foot.addWidget(add_group)
        foot.addStretch()
        layout = QVBoxLayout(self)
        self.hint = QLabel(
            "No rules: every track qualifies. Add a rule or a group."
            if not remove
            else "Empty group: add a rule or a group, or remove the group."
        )
        self.hint.setStyleSheet("color: gray")
        layout.addLayout(head)
        layout.addWidget(self.hint)
        layout.addLayout(self.items_layout)
        layout.addLayout(foot)
        for item in group["rules"]:
            if "field" in item:
                self.add_rule(item)
            else:
                self.add_group(item)
        self._update_hint()

    def _update_hint(self) -> None:
        self.hint.setVisible(not self.items)

    def _add(self, widget) -> None:
        self.items.append(widget)
        self.items_layout.addWidget(widget)
        self._update_hint()

    def add_rule(self, rule) -> None:
        self._add(RuleRow(rule, self.remove_item))

    def add_group(self, group) -> None:
        # A new group (group None: a blank one with a starter rule).  A blank
        # starter rule that has not been
        # used yet makes way for it, so a playlist can begin with a group.
        if group is None:
            starters = [
                w for w in self.items if isinstance(w, RuleRow) and w.starter
            ]
            if len(starters) == len(self.items):
                for w in starters:
                    self.remove_item(w)
        widget = GroupWidget(group, self.remove_item)
        if group is None:
            widget.add_rule(None)
        self._add(widget)

    def remove_item(self, widget) -> None:
        self.items.remove(widget)
        widget.setParent(None)
        widget.deleteLater()
        self._update_hint()

    def data(self) -> dict:
        return {
            "match": self.match.currentData(),
            "rules": [
                w.data() if isinstance(w, GroupWidget) else w.rule()
                for w in self.items
            ],
        }


def match_count(db, rules: dict) -> int:
    # How many library songs (duplicates excluded) a dynamic playlist's rules
    # select.
    return len(dyn.matches(db, rules))


class DynamicRulesDialog(QDialog):
    # Name, rules (with groups), and sort order of a dynamic playlist.

    def __init__(self, parent, name: str = "", rules=None, db=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(
            "Dynamic playlist" if not name else f"Rules of \u201c{name}\u201d"
        )
        rules = rules or dyn.EMPTY_RULES
        self.name = QLineEdit(name)
        self.root = GroupWidget(rules)
        if not rules["rules"] and not name:
            self.root.add_rule(None)
        self.sort = QComboBox()
        for key, label in dyn.SORTS.items():
            self.sort.addItem(label, key)
        self.sort.setCurrentIndex(
            max(self.sort.findData(rules["sort"]["field"]), 0)
        )
        self.prefer = QLineEdit(", ".join(rules.get("prefer", [])))
        self.prefer.setPlaceholderText(
            "e.g. flac, m4a, mp3 (empty = no preference)"
        )
        self.prefer.setToolTip(
            "File types in order of preference: the list is filled with the"
            " first type and goes on to\nthe next only when there are not"
            " enough tracks of the types before it."
        )
        self.descending = QCheckBox("descending")
        self.descending.setChecked(bool(rules["sort"].get("descending")))
        top = QFormLayout()
        top.addRow("Name:", self.name)
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Choose in the order:"))
        sort_row.addWidget(self.sort)
        sort_row.addWidget(self.descending)
        sort_row.addStretch()
        prefer_row = QHBoxLayout()
        prefer_row.addWidget(QLabel("Prefer file types:"))
        prefer_row.addWidget(self.prefer, 1)
        note = QLabel(
            "Duplicates are never selected. The list is filled to its length"
            " (set in the Playlists tab) from the first tracks in this order,"
            " or random ones; a track leaves the list when it has been played"
            " and a new one is added at the end."
        )
        note.setWordWrap(True)
        self.count_label = QLabel()
        count_button = QPushButton("Count matches")
        count_button.setToolTip(
            "How many songs in the library the rules above select (duplicates"
            " are not counted)"
        )
        count_button.clicked.connect(self.count_matches)
        count_row = QHBoxLayout()
        count_row.addWidget(count_button)
        count_row.addWidget(self.count_label, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.root)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(scroll, 1)
        layout.addLayout(sort_row)
        layout.addLayout(prefer_row)
        if db is not None:
            layout.addLayout(count_row)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.resize(680, 460)
        if db is not None and rules is not dyn.EMPTY_RULES and name:
            self.count_matches()

    def count_matches(self) -> None:
        error = dyn.validate(self.rules())
        if error:
            self.count_label.setText(error)
            return
        n = match_count(self.db, self.rules())
        self.count_label.setText(f"{n} matched")

    def rules(self) -> dict:
        rules = {
            **self.root.data(),
            "sort": {
                "field": self.sort.currentData(),
                "descending": self.descending.isChecked(),
            },
        }
        prefer = dyn.parse_prefer(self.prefer.text())
        if prefer:
            rules["prefer"] = prefer
        return rules

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(
                self, "Dynamic playlist", "Give the playlist a name."
            )
            return
        error = dyn.validate(self.rules())
        if error:
            QMessageBox.warning(self, "Dynamic playlist", error)
            return
        self.accept()


class OrderedPicker(QWidget):
    # An ordered list of playlists or containers (an item may appear more than
    # once if repeats is True) with Add,
    # Remove, Move up and Move down.

    def __init__(
        self,
        label: str,
        choices,
        repeats: bool,
        current: list[int],
        names: dict[int, str],
        parent=None,
    ):
        super().__init__(parent)
        self.choices, self.repeats, self.names = (
            choices,
            repeats,
            names,
        )  # choices() -> [(id, name)] that may be added
        self.list = QListWidget()
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.add_button = QPushButton("Add...")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        buttons = QHBoxLayout()
        for b in (
            self.add_button,
            self.remove_button,
            self.up_button,
            self.down_button,
        ):
            buttons.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label))
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        for pid in current:
            self._append(pid)

    def _append(self, pid: int) -> None:
        item = QListWidgetItem(self.names.get(pid, f"#{pid}"))
        item.setData(ID_ROLE, pid)
        self.list.addItem(item)
        self.list.setCurrentItem(item)

    def ids(self) -> list[int]:
        return [
            self.list.item(r).data(ID_ROLE) for r in range(self.list.count())
        ]

    def _add(self) -> None:
        have = set() if self.repeats else set(self.ids())
        options = [
            (pid, name) for pid, name in self.choices() if pid not in have
        ]
        if not options:
            QMessageBox.information(
                self, "Add", "There is nothing more to add."
            )
            return
        menu = QMenu(self)
        for pid, name in options:
            menu.addAction(name).triggered.connect(
                lambda _=False, pid=pid: self._append(pid)
            )
        menu.exec(
            self.add_button.mapToGlobal(self.add_button.rect().bottomLeft())
        )

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.list.count():
            return
        self.list.insertItem(target, self.list.takeItem(row))
        self.list.setCurrentRow(target)


class ContainerDialog(QDialog):
    # Name, time limit and playlists of a container.

    def __init__(self, parent, db, container_id: int | None = None):
        super().__init__(parent)
        self.setWindowTitle(
            "Container" if container_id is None else "Edit container"
        )
        self.resize(480, 520)
        kinds = db.kinds()
        names = {pid: name for pid, name, _c in db.playlists()}
        info = (
            db.container_info(container_id)
            if container_id is not None
            else None
        )
        self.name = QLineEdit(names.get(container_id, ""))
        self.limited = QCheckBox("Time limit")
        limit = (info or {}).get("limit_seconds", 300)
        self.limited.setChecked(limit is not None)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 600)
        self.minutes.setSuffix(" min")
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 59)
        self.seconds.setSuffix(" s")
        self.minutes.setValue((limit or 0) // 60)
        self.seconds.setValue((limit or 0) % 60)
        self.limited.toggled.connect(
            lambda on: (
                self.minutes.setEnabled(on),
                self.seconds.setEnabled(on),
            )
        )
        self.minutes.setEnabled(self.limited.isChecked())
        self.seconds.setEnabled(self.limited.isChecked())
        limit_row = QHBoxLayout()
        for w in (self.limited, self.minutes, self.seconds):
            limit_row.addWidget(w)
        limit_row.addStretch()
        self.end_mode = QComboBox()
        for key, label in (
            ("fade", "fade out, then go on"),
            ("cut", "cut off, then go on"),
            ("finish", "let the track finish, then go on"),
        ):
            self.end_mode.addItem(label, key)
        self.end_mode.setCurrentIndex(
            max(self.end_mode.findData((info or {}).get("end_mode", "fade")), 0)
        )
        self.when_empty = QComboBox()
        for key, label in (
            ("repeat", "start the playlists again"),
            ("next", "go on to the next section early"),
        ):
            self.when_empty.addItem(label, key)
        self.when_empty.setCurrentIndex(
            max(
                self.when_empty.findData(
                    (info or {}).get("when_empty", "repeat")
                ),
                0,
            )
        )
        self.picker = OrderedPicker(
            "Playlists (played in this order; a dynamic playlist needs a time"
            " limit)",
            lambda: [
                (pid, name)
                for pid, name, _c in db.playlists()
                if kinds.get(pid) in ("manual", "dynamic")
            ],
            False,
            db.members(container_id) if container_id is not None else [],
            names,
        )
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("", limit_row)
        form.addRow("At the limit", self.end_mode)
        form.addRow("If the playlists run out", self.when_empty)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.picker, 1)
        layout.addWidget(buttons)

    def limit_seconds(self) -> int | None:
        return (
            self.minutes.value() * 60 + self.seconds.value()
            if self.limited.isChecked()
            else None
        )

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Container", "Give the container a name.")
        elif self.limited.isChecked() and self.limit_seconds() < 1:
            QMessageBox.warning(
                self, "Container", "The time limit must be at least one second."
            )
        else:
            self.accept()


class SequenceDialog(QDialog):
    # Name and containers of a sequence.

    def __init__(self, parent, db, sequence_id: int | None = None):
        super().__init__(parent)
        self.setWindowTitle(
            "Sequence" if sequence_id is None else "Edit sequence"
        )
        self.resize(460, 440)
        kinds = db.kinds()
        names = {pid: name for pid, name, _c in db.playlists()}
        self.name = QLineEdit(names.get(sequence_id, ""))
        self.picker = OrderedPicker(
            "Containers (played one after another, then play stops; a container"
            " may be used more than once)",
            lambda: [
                (pid, name)
                for pid, name, _c in db.playlists()
                if kinds.get(pid) == "container"
            ],
            True,
            db.members(sequence_id) if sequence_id is not None else [],
            names,
        )
        form = QFormLayout()
        form.addRow("Name", self.name)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.picker, 1)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Sequence", "Give the sequence a name.")
        elif not self.picker.ids():
            QMessageBox.warning(self, "Sequence", "Add at least one container.")
        else:
            self.accept()


def populate_add_menu(
    menu: QMenu, db, paths: list[str], parent: QWidget, done
) -> None:
    # Add "Add to playlist" entries to menu.  done(playlist_id) is called after
    # files were added.
    def add(playlist_id: int | None) -> None:
        if playlist_id is None:
            playlist_id = create_playlist(parent, db)
            if playlist_id is None:
                return
        try:
            db.add_to_playlist(playlist_id, paths)
        except sqlite3.Error as e:
            QMessageBox.critical(parent, "Cannot add to playlist", str(e))
            return
        done(playlist_id)

    sub = menu.addMenu(
        f"Add {len(paths)} track{'s' if len(paths) != 1 else ''} to playlist"
    )
    sub.setEnabled(bool(paths))
    kinds = (
        db.kinds()
        # dynamic playlists select their own tracks; containers and sequences
        # hold
        # playlists
    )
    for pid, name, _count in db.playlists():
        if kinds.get(pid) == "manual":
            sub.addAction(name).triggered.connect(
                lambda _=False, pid=pid: add(pid)
            )
    if sub.actions():
        sub.addSeparator()
    sub.addAction("New playlist...").triggered.connect(
        lambda _=False: add(None)
    )


class PlaylistPanel(QWidget):
    changed = pyqtSignal(
        int
        # a playlist's entries changed (or it was created, renamed or deleted:
        # -1)
    )
    play_requested = pyqtSignal(
        int, int, object
    )  # playlist id, entry row to start at (-1 = default), shuffle (bool)

    def __init__(self, db, can_play: bool = False, parent=None):
        super().__init__(parent)
        self.db = db
        self.can_play = can_play
        self._loading = False
        self._matched = 0  # the dynamic playlist shown: songs its rules select

        self.lists = QListWidget()
        self.lists.currentItemChanged.connect(lambda *_: self._load_entries())
        self.new_button = QPushButton("New...")
        self.new_dynamic_button = QPushButton("New dynamic...")
        self.new_dynamic_button.setToolTip(
            "A playlist whose tracks are selected by rules"
        )
        self.edit_rules_button = QPushButton("Edit Rules")
        self.refresh_button = QPushButton("Regenerate")
        self.refresh_button.setToolTip(
            "Select the tracks of this dynamic playlist afresh"
        )
        self.length = QSpinBox()
        self.length.setRange(1, 500)
        self.length.setValue(db.dynamic_length())
        self.length.setToolTip("How many tracks a dynamic playlist holds")
        self.length.editingFinished.connect(self._length_changed)
        self.new_dynamic_button.clicked.connect(self.new_dynamic)
        self.edit_rules_button.clicked.connect(self.edit_rules)
        self.refresh_button.clicked.connect(self.regenerate)
        self.new_container_button = QPushButton("New container...")
        self.new_container_button.setToolTip(
            "A timed segment: one or more playlists played for a set time"
        )
        self.new_sequence_button = QPushButton("New sequence...")
        self.new_sequence_button.setToolTip(
            "Containers played one after another, then play stops"
        )
        self.edit_button = QPushButton("Edit...")
        self.edit_button.setToolTip(
            "Change the time limit and playlists of a container, or the"
            " containers of a sequence"
        )
        self.new_container_button.clicked.connect(self.new_container)
        self.new_sequence_button.clicked.connect(self.new_sequence)
        self.edit_button.clicked.connect(self.edit_composite)
        self.rename_button = QPushButton("Rename...")
        self.delete_button = QPushButton("Delete")
        self.new_button.clicked.connect(self.new_playlist)
        self.rename_button.clicked.connect(self.rename_playlist)
        self.delete_button.clicked.connect(self.delete_playlist)
        list_buttons = QHBoxLayout()
        for b in (self.new_button, self.rename_button, self.delete_button):
            list_buttons.addWidget(b)
        upper = QWidget()
        top_layout = QVBoxLayout(upper)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(QLabel("Playlists"))
        top_layout.addWidget(self.lists, 1)
        top_layout.addLayout(list_buttons)
        dynamic_buttons = QHBoxLayout()
        for b in (
            self.new_dynamic_button,
            self.edit_rules_button,
            self.refresh_button,
        ):
            dynamic_buttons.addWidget(b)
        dynamic_buttons.addStretch()
        dynamic_buttons.addWidget(QLabel("Dynamic length"))
        dynamic_buttons.addWidget(self.length)
        top_layout.addLayout(dynamic_buttons)
        composite_buttons = QHBoxLayout()
        for b in (
            self.new_container_button,
            self.new_sequence_button,
            self.edit_button,
        ):
            composite_buttons.addWidget(b)
        composite_buttons.addStretch()
        top_layout.addLayout(composite_buttons)
        self.rules_view = QPlainTextEdit()
        self.rules_view.setReadOnly(True)
        self.rules_view.setMinimumHeight(40)
        self.rules_view.setToolTip(
            "The rules of this dynamic playlist; Edit Rules changes them. Drag"
            " the divider above to resize."
        )
        self.rules_view.hide()
        top = QSplitter(
            Qt.Orientation.Vertical
        )  # the rules get more room by dragging the divider up
        top.addWidget(upper)
        top.addWidget(self.rules_view)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 1)
        top.setCollapsible(0, False)
        top.setSizes([200, 100])

        self.entries = QListWidget()
        self.entries.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.entries.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.entries.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.entries.model().rowsMoved.connect(lambda *_: self._order_changed())
        self.entries.itemSelectionChanged.connect(self._update_buttons)
        self.entries.itemDoubleClicked.connect(
            lambda item: self._play(self.entries.row(item), None)
        )
        QShortcut(
            QKeySequence.StandardKey.Delete,
            self.entries,
            self.remove_selected,
            context=Qt.ShortcutContext.WidgetShortcut,
        )
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.remove_button = QPushButton("Remove")
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.remove_button.clicked.connect(self.remove_selected)
        entry_buttons = QHBoxLayout()
        if can_play:
            self.play_button = QPushButton("Play")
            self.shuffle_button = QPushButton("Shuffle")
            self.play_button.setToolTip(
                "Play the playlist in order from the selected entry (or the"
                " top)"
            )
            self.shuffle_button.setToolTip(
                "Play the playlist in random order; no track repeats until all"
                " have played"
            )
            self.play_button.clicked.connect(
                lambda: self._play(self._first_selected_row(), False)
            )
            self.shuffle_button.clicked.connect(lambda: self._play(-1, True))
            entry_buttons.addWidget(self.play_button)
            entry_buttons.addWidget(self.shuffle_button)
        for b in (self.up_button, self.down_button, self.remove_button):
            entry_buttons.addWidget(b)
        self.entries_label = QLabel()
        self.details = QTreeWidget()  # a dynamic playlist: why each track
        self.details.setRootIsDecorated(False)
        self.details.setUniformRowHeights(True)
        self.details.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.details.itemSelectionChanged.connect(self._details_selected)
        self.details.itemDoubleClicked.connect(
            lambda item: self._play(
                self.details.indexOfTopLevelItem(item), None
            )
        )
        self.details.setVisible(False)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(self.entries_label)
        bottom_layout.addWidget(self.entries, 1)
        bottom_layout.addWidget(self.details, 1)
        bottom_layout.addLayout(entry_buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([300, 400])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.refresh()

    # -- showing -------------------------------------------------------------

    def set_db(self, db) -> None:
        self.db = db
        self.length.setValue(db.dynamic_length())
        self.refresh()

    def current_id(self) -> int | None:
        item = self.lists.currentItem()
        return None if item is None else item.data(ID_ROLE)

    def refresh(self, select: int | None = None) -> None:
        # Reload the list of playlists, keeping (or choosing) the current one.
        keep = select if select is not None else self.current_id()
        self._loading = True
        self.lists.clear()
        kinds = self.db.kinds()
        for pid, name, count in self.db.playlists():
            item = QListWidgetItem(
                self.list_text(pid, name, count, kinds.get(pid, "manual"))
            )
            item.setData(ID_ROLE, pid)
            self.lists.addItem(item)
            if pid == keep:
                self.lists.setCurrentItem(item)
        self._loading = False
        if self.lists.currentItem() is None and self.lists.count():
            self.lists.setCurrentRow(0)
        else:
            self._load_entries()

    def list_text(self, pid: int, name: str, count: int, kind: str) -> str:
        if kind == "dynamic":
            return f"\u25c6 {name}  ({count})"
        if kind == "container":
            info = self.db.container_info(pid) or {}
            limit = info.get("limit_seconds")
            members = len(self.db.members(pid))
            return (
                f"\u25a3 {name} "
                f" ({'no limit' if limit is None else fmt_clock(limit * 1000)},"
                f" {members} playlist{'s' if members != 1 else ''})"
            )
        if kind == "sequence":
            sections = containers.sections_for(self.db, pid)
            total = (
                None
                if any(s.limit_ms is None for s in sections)
                else sum(s.limit_ms for s in sections)
            )
            return (
                f"\u21c9 {name}  ({len(sections)}"
                f" section{'s' if len(sections) != 1 else ''}"
                f"{'' if total is None else ', ' + fmt_clock(total)})"
            )
        return f"{name}  ({count})"

    def entry_text(self, path: str) -> str:
        tags = self.db.tags.get(path, {})
        title = next(
            (v for v in tags.get("TITLE", []) if v.strip()),
            os.path.splitext(os.path.basename(path))[0],
        )
        artist = "; ".join(tags.get("ARTIST", []))
        return f"{title} — {artist}" if artist else title

    def _load_entries(self) -> None:
        if self._loading:
            return
        pid = self.current_id()
        self._loading = True
        self.entries.clear()
        kind = self.db.kinds().get(pid, "manual") if pid is not None else None
        if kind in ("container", "sequence"):
            names = {p: n for p, n, _c in self.db.playlists()}
            for position, member in enumerate(self.db.members(pid)):
                item = QListWidgetItem(
                    f"{position + 1}. {names.get(member, '?')}"
                )
                item.setData(ID_ROLE, member)
                self.entries.addItem(item)
        elif pid is not None:
            for entry, path in self.db.playlist_entries(pid):
                item = QListWidgetItem(self.entry_text(path))
                item.setData(ID_ROLE, entry)
                item.setData(PATH_ROLE, path)
                item.setToolTip(path)
                self.entries.addItem(item)
        self._loading = False
        dynamic = kind == "dynamic"
        songs = dyn.song_stats(self.db) if dynamic else None
        self._matched = (  # counted here only: selecting rows asks for it
            len(dyn.matches(self.db, self.db.get_rules(pid), songs=songs))
            if dynamic
            else 0
        )
        self._fill_details(pid if dynamic else None, songs)
        composite = kind in ("container", "sequence")
        if pid is None:
            label = "Select or create a playlist"
        elif kind == "container":
            n = self.entries.count()
            label = (
                f"Container \u2014 {n} playlist{'s' if n != 1 else ''}, played"
                " in this order for the time limit; Edit... changes it"
            )
        elif kind == "sequence":
            n = self.entries.count()
            label = (
                f"Sequence \u2014 {n} section{'s' if n != 1 else ''}, played"
                " one after another; Edit... changes it"
            )
        elif dynamic:
            label = (
                f"{self.entries.count()} tracks \u2014 dynamic,"
                f" {self._matched} matched"
            )
        else:
            label = (
                f"{self.entries.count()} entries \u2014 drag to reorder, Delete"
                " removes"
            )
        self.entries_label.setText(label)
        self.entries.setDragDropMode(
            QAbstractItemView.DragDropMode.NoDragDrop
            if dynamic or composite
            else QAbstractItemView.DragDropMode.InternalMove
        )
        self._update_buttons()

    def _fill_details(self, pid: int | None, songs) -> None:
        # A dynamic playlist shows the values its rules test, so that it is
        # clear why each track was chosen; other lists show titles only.
        self.details.setVisible(pid is not None)
        self.entries.setVisible(pid is None)
        self.details.clear()
        if pid is None:
            return
        rules = self.db.get_rules(pid)
        fields = ["title", "artist", "album"] + [
            f
            for f in dyn.rule_fields(rules)
            if f not in ("title", "artist", "album")
        ]
        now = datetime.now(timezone.utc)
        headers = []
        for f in fields:
            name = dyn.FIELDS[f].split(" (")[0]
            headers.append(
                name + " (any copy)" if f in ("plays", "last_played") else name
            )
        self.details.setColumnCount(len(fields))
        self.details.setHeaderLabels(headers)
        for entry, path in self.db.playlist_entries(pid):
            item = QTreeWidgetItem(
                [dyn.field_text(self.db, path, f, songs, now) for f in fields]
            )
            item.setToolTip(0, path)
            self.details.addTopLevelItem(item)
        for c in range(len(fields)):
            self.details.resizeColumnToContents(c)
            self.details.setColumnWidth(
                c, min(self.details.columnWidth(c), 320)
            )

    def _details_selected(self) -> None:
        # keep the list's selection (the Play button reads it) in step
        rows = {
            self.details.indexOfTopLevelItem(i)
            for i in self.details.selectedItems()
        }
        self.entries.blockSignals(True)
        self.entries.clearSelection()
        for r in rows:
            self.entries.item(r).setSelected(True)
        self.entries.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self) -> None:
        pid = self.current_id()
        has_list = pid is not None
        kind = self.db.kinds().get(pid, "manual") if has_list else None
        dynamic = kind == "dynamic"
        selected = (
            bool(self.entries.selectedItems()) and kind == "manual"
        )  # other lists' entries are not edited by hand
        self.edit_button.setEnabled(kind in ("container", "sequence"))
        self.edit_rules_button.setEnabled(dynamic)
        rules = self.db.get_rules(pid) if dynamic else None
        total = self._matched if rules else 0
        self.rules_view.setPlainText(
            (f"{total} matched\n\n" + dyn.describe(rules)) if rules else ""
        )
        self.rules_view.setVisible(bool(rules))
        self.refresh_button.setEnabled(dynamic)
        self.rename_button.setEnabled(has_list)
        self.delete_button.setEnabled(has_list)
        self.up_button.setEnabled(selected)
        self.down_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)
        if self.can_play:
            self.play_button.setEnabled(self.entries.count() > 0)
            shuffles = self.entries.count() > 0 and not (
                has_list and self.db.holds_dynamic(pid)
            )
            self.shuffle_button.setEnabled(shuffles)
            self.shuffle_button.setToolTip(
                "Play the playlist in random order; no track repeats until"
                " all have played"
                if not has_list or not self.db.holds_dynamic(pid)
                else "A dynamic playlist plays in order"
            )

    # -- playlists -----------------------------------------------------------

    def new_playlist(self) -> None:
        pid = create_playlist(self, self.db)
        if pid is not None:
            self.refresh(select=pid)
            self.changed.emit(-1)

    def new_container(self) -> None:
        dialog = ContainerDialog(self, self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            pid = self.db.create_container(
                dialog.name.text().strip(),
                dialog.limit_seconds(),
                dialog.end_mode.currentData(),
                dialog.when_empty.currentData(),
                dialog.picker.ids(),
            )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Container",
                "There is already a container with that name.",
            )
            return
        except ValueError as e:
            QMessageBox.warning(self, "Container", str(e))
            return
        self.refresh(select=pid)
        self.changed.emit(-1)

    def new_sequence(self) -> None:
        dialog = SequenceDialog(self, self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            pid = self.db.create_sequence(
                dialog.name.text().strip(), dialog.picker.ids()
            )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self, "Sequence", "There is already a sequence with that name."
            )
            return
        except ValueError as e:
            QMessageBox.warning(self, "Sequence", str(e))
            return
        self.refresh(select=pid)
        self.changed.emit(-1)

    def edit_composite(self) -> None:
        pid = self.current_id()
        kind = self.db.kinds().get(pid) if pid is not None else None
        if kind not in ("container", "sequence"):
            return
        name = next(n for i, n, _c in self.db.playlists() if i == pid)
        dialog = (
            ContainerDialog(self, self.db, pid)
            if kind == "container"
            else SequenceDialog(self, self.db, pid)
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if dialog.name.text().strip() != name:
                self.db.rename_playlist(pid, dialog.name.text().strip())
            if kind == "container":
                self.db.update_container(
                    pid,
                    dialog.limit_seconds(),
                    dialog.end_mode.currentData(),
                    dialog.when_empty.currentData(),
                    dialog.picker.ids(),
                )
            else:
                self.db.update_sequence(pid, dialog.picker.ids())
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self, "Edit", "There is already one with that name."
            )
            return
        except ValueError as e:
            QMessageBox.warning(self, "Edit", str(e))
            return
        self.refresh(select=pid)
        self.changed.emit(pid)

    def _length_changed(self) -> None:
        if self.length.value() != self.db.dynamic_length():
            self.db.set_dynamic_length(
                self.length.value()
            )  # applies the next time a list is filled

    def new_dynamic(self) -> None:
        dialog = DynamicRulesDialog(self, db=self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            pid = self.db.create_dynamic(
                dialog.name.text().strip(), dialog.rules()
            )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Dynamic playlist",
                "There is already a playlist with that name.",
            )
            return
        except ValueError as e:
            QMessageBox.warning(self, "Dynamic playlist", str(e))
            return
        self.refresh(select=pid)
        self.changed.emit(-1)

    def edit_rules(self) -> None:
        pid = self.current_id()
        rules = None if pid is None else self.db.get_rules(pid)
        if rules is None:
            return
        name = next(n for i, n, _c in self.db.playlists() if i == pid)
        dialog = DynamicRulesDialog(self, name, rules, db=self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if dialog.name.text().strip() != name:
                self.db.rename_playlist(pid, dialog.name.text().strip())
            self.db.set_rules(pid, dialog.rules())
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Dynamic playlist",
                "There is already a playlist with that name.",
            )
            return
        except ValueError as e:
            QMessageBox.warning(self, "Dynamic playlist", str(e))
            return
        self.refresh(select=pid)
        self.changed.emit(pid)

    def regenerate(self) -> None:
        pid = self.current_id()
        if pid is None or pid not in self.db.dynamic_ids():
            return
        self.db.regenerate(pid)
        self.refresh(select=pid)
        self.changed.emit(pid)

    def rename_playlist(self) -> None:
        pid = self.current_id()
        if pid is None:
            return
        old = next(n for i, n, _c in self.db.playlists() if i == pid)
        name = ask_new_name(self, "Rename playlist", old)
        if name is None or name == old:
            return
        try:
            self.db.rename_playlist(pid, name)
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self, "Rename", f"There is already one named “{name}”."
            )
            return
        self.refresh(select=pid)
        self.changed.emit(-1)

    def delete_playlist(self) -> None:
        pid = self.current_id()
        if pid is None:
            return
        name = next(n for i, n, _c in self.db.playlists() if i == pid)
        used = self.db.used_in(pid)
        warning = (
            (
                "\nIt will be removed from: "
                + ", ".join(n for _i, n in used)
                + "."
            )
            if used
            else ""
        )
        if (
            QMessageBox.question(
                self,
                "Delete playlist",
                f"Delete “{name}”?{warning}\n"
                "The tracks themselves are not touched.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.db.delete_playlist(pid)
        self.refresh()
        self.changed.emit(-1)

    # -- entries -------------------------------------------------------------

    def _entry_ids(self) -> list[int]:
        return [
            self.entries.item(r).data(ID_ROLE)
            for r in range(self.entries.count())
        ]

    def _order_changed(self) -> None:
        # Entries were dragged to a new order.
        pid = self.current_id()
        if pid is None or self._loading:
            return
        self.db.set_playlist_order(pid, self._entry_ids())
        self.changed.emit(pid)

    def _selected_rows(self) -> list[int]:
        return sorted(self.entries.row(i) for i in self.entries.selectedItems())

    def _first_selected_row(self) -> int:
        rows = self._selected_rows()
        return rows[0] if rows else -1

    def move_selected(self, step: int) -> None:
        pid, rows = self.current_id(), self._selected_rows()
        if pid is None or not rows:
            return
        ids = self._entry_ids()
        chosen = {ids[r] for r in rows}
        if step < 0:
            for r in rows:
                if r > 0 and ids[r - 1] not in chosen:
                    ids[r - 1], ids[r] = ids[r], ids[r - 1]
        else:
            for r in reversed(rows):
                if r + 1 < len(ids) and ids[r + 1] not in chosen:
                    ids[r + 1], ids[r] = ids[r], ids[r + 1]
        self.db.set_playlist_order(pid, ids)
        self._load_entries()
        for r in range(self.entries.count()):
            if self.entries.item(r).data(ID_ROLE) in chosen:
                self.entries.item(r).setSelected(True)
        self.changed.emit(pid)

    def remove_selected(self) -> None:
        pid, rows = self.current_id(), self._selected_rows()
        if pid is None or not rows:
            return
        self.db.remove_from_playlist(
            pid, [self.entries.item(r).data(ID_ROLE) for r in rows]
        )
        self.refresh()
        self.changed.emit(pid)

    def _play(self, row: int, shuffle) -> bool:
        pid = self.current_id()
        if self.can_play and pid is not None and self.entries.count():
            self.play_requested.emit(pid, row, shuffle)
            return True
        return False

    def play_selected(self) -> bool:
        # Play the selected playlist from the selected entry (or its start,
        # or a random one when shuffling), leaving the shuffle button as it
        # is.  False when there is nothing to play.
        return self._play(self._first_selected_row(), None)
