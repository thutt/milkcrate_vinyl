# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Dialogs for reviewing the results of scanning the music folder.
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QProgressDialog,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

import library_scan


def _progress(parent, text: str) -> QProgressDialog:
    box = QProgressDialog(text, "Cancel", 0, 0, parent)
    box.setWindowModality(Qt.WindowModality.WindowModal)
    box.setMinimumDuration(0)
    box.show()
    return box


def scan_library(parent, db, normalize) -> bool:
    # The whole flow: scan, review, apply, then the duplicate review.  True if
    # the database changed.
    root = db.root()
    if not root or not os.path.isdir(root):
        QMessageBox.warning(
            parent,
            "Scan for new files",
            f"The music folder is not available:\n{root or '(none recorded)'}",
        )
        return False
    box = _progress(parent, f"Scanning {root} ...")

    def tick(n: int) -> bool:
        QApplication.processEvents()
        box.setLabelText(f"Scanning {root}: {n} files ...")
        return not box.wasCanceled()

    result = library_scan.scan(db, root, tick)
    box.close()
    if result is None:
        return False
    if not (result.new or result.missing or result.changed):
        QMessageBox.information(
            parent,
            "Scan for new files",
            f"Nothing to do: all {result.unchanged} files match the database.",
        )
        return False

    box = _progress(
        parent,
        f"Reading the tags of {len(result.new) + len(result.changed)}"
        " files ...",
    )
    box.setMaximum(len(result.new) + len(result.changed))

    def read_tick(n: int) -> bool:
        box.setValue(n)
        QApplication.processEvents()
        return not box.wasCanceled()

    entries = library_scan.read_files(
        root, result.new + result.changed, progress=read_tick
    )
    box.close()
    if entries is None:
        return False

    dialog = ScanDialog(parent, db, result, entries)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    added = dialog.apply()
    groups = (
        library_scan.duplicate_groups(db, dialog.added, normalize)
        if dialog.added
        else []
    )
    if groups:
        DuplicatesDialog(parent, db, groups).exec_apply()
    return True


class ScanDialog(QDialog):
    def __init__(
        self, parent, db, result: library_scan.Scan, entries: dict[str, dict]
    ):
        super().__init__(parent)
        self.setWindowTitle("Scan for new files")
        self.resize(900, 600)
        self.db, self.result, self.entries = db, result, entries
        self.added: list[str] = []
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"Music folder: {result.root}      {result.unchanged} files"
                " unchanged"
            )
        )
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.album = QCheckBox(
            "Set ALBUM from the folder name where a new file has none"
        )
        self.album.setChecked(True)
        self.album.toggled.connect(self._fill_new)
        self.new_tree = self._tree(
            ["File", "Title", "Artist", "Album", "Cleanup"]
        )
        self.missing_tree = self._tree(
            ["File", "Title", "Artist", "Album", "Plays"]
        )
        self.changed_tree = self._tree([
            "File",
            "Title (stored)",
            "Artist (stored)",
            "Title (on disk)",
            "Artist (on disk)",
        ])
        self.tabs.addTab(
            self._page(
                self.new_tree,
                self.album,
                "New files are added with their tags. Untick a file to leave it"
                " out.",
            ),
            "",
        )
        self.tabs.addTab(
            self._page(
                self.missing_tree,
                None,
                "These files are in the database but no longer under the music"
                " folder. Tick the ones to remove from the database (with their"
                " play counts, ratings and playlist entries); nothing is"
                " deleted on disk. If a drive is not mounted, leave them"
                " alone.",
            ),
            "",
        )
        self.tabs.addTab(
            self._page(
                self.changed_tree,
                None,
                "These files changed on disk. Re-reading replaces the stored"
                " tags, so any edits made in the database are lost. Tick the"
                " ones to re-read.",
            ),
            "",
        )
        self._fill_new()
        for path in result.missing:
            tags = db.tags[path]
            self._row(
                self.missing_tree,
                path,
                [
                    path,
                    _first(tags, "TITLE"),
                    _first(tags, "ARTIST"),
                    _first(tags, "ALBUM"),
                    str(db.stats.get(path, (None, 0, None))[1]),
                ],
                False,
            )
        for path in result.changed:
            tags, new = db.tags[path], entries[path].get("tags") or {}
            self._row(
                self.changed_tree,
                path,
                [
                    path,
                    _first(tags, "TITLE"),
                    _first(tags, "ARTIST"),
                    _first(new, "TITLE"),
                    _first(new, "ARTIST"),
                ],
                False,
            )
        self._titles()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _tree(self, headers: list[str]) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels(headers)
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 420)
        tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        tree.itemChanged.connect(lambda *_: self._titles())
        return tree

    def _page(self, tree: QTreeWidget, option, note: str):
        from PyQt6.QtWidgets import QWidget

        page = QWidget()
        box = QVBoxLayout(page)
        label = QLabel(note)
        label.setWordWrap(True)
        box.addWidget(label)
        if option:
            box.addWidget(option)
        row = QHBoxLayout()
        for text, which in (
            ("Tick all", "all"),
            ("Untick all", "none"),
            ("Tick selected rows", "sel+"),
            ("Untick selected rows", "sel-"),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, w=which: self._tick(tree, w))
            row.addWidget(button)
        row.addStretch()
        box.addLayout(row)
        box.addWidget(tree)
        return page

    def _tick(self, tree: QTreeWidget, which: str) -> None:
        # Tick or untick every row, or only the selected rows (select many with
        # Shift or Ctrl, or Ctrl+A).
        items = (
            [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
            if which in ("all", "none")
            else tree.selectedItems()
        )
        state = (
            Qt.CheckState.Checked
            if which in ("all", "sel+")
            else Qt.CheckState.Unchecked
        )
        tree.blockSignals(True)
        for item in items:
            item.setCheckState(0, state)
        tree.blockSignals(False)
        self._titles()

    def _row(
        self, tree: QTreeWidget, key: str, cells: list[str], checked: bool
    ) -> None:
        item = QTreeWidgetItem(cells)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        tree.addTopLevelItem(item)

    def _fill_new(self) -> None:
        old = {
            self.new_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole): (
                self.new_tree.topLevelItem(i).checkState(0)
            )
            for i in range(self.new_tree.topLevelItemCount())
        }
        self.new_tree.blockSignals(True)
        self.new_tree.clear()
        for path in self.result.new:
            tags, notes = library_scan.clean(
                path, self.entries[path], self.album.isChecked()
            )
            error = self.entries[path].get("error")
            self._row(
                self.new_tree,
                path,
                [
                    path,
                    _first(tags, "TITLE"),
                    _first(tags, "ARTIST"),
                    _first(tags, "ALBUM"),
                    f"unreadable: {error}" if error else "; ".join(notes),
                ],
                True,
            )
            if old.get(path) == Qt.CheckState.Unchecked:
                self.new_tree.topLevelItem(
                    self.new_tree.topLevelItemCount() - 1
                ).setCheckState(0, Qt.CheckState.Unchecked)
        self.new_tree.blockSignals(False)
        self._titles()

    @staticmethod
    def _checked(tree: QTreeWidget) -> list[str]:
        return [
            tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked
        ]

    def _titles(self) -> None:
        for n, (tree, name) in enumerate((
            (self.new_tree, "New"),
            (self.missing_tree, "Missing"),
            (self.changed_tree, "Changed"),
        )):
            self.tabs.setTabText(
                n,
                f"{name} ({len(self._checked(tree))} of"
                f" {tree.topLevelItemCount()})",
            )

    def apply(self) -> None:
        # Back up, then add, remove and re-read the ticked files, writing a
        # journal line for each.
        new, missing, changed = (
            self._checked(t)
            for t in (self.new_tree, self.missing_tree, self.changed_tree)
        )
        if not (new or missing or changed):
            return
        backup = self.db.backup("scan")
        records = []
        new_entries = {}
        for path in new:
            tags, notes = library_scan.clean(
                path, self.entries[path], self.album.isChecked()
            )
            new_entries[path] = (self.entries[path], tags)
            records.append(
                {"action": "add", "path": path, "tags": tags, "cleanup": notes}
            )
        for path in changed:
            tags = self.entries[path].get("tags") or {}
            tags, notes = library_scan.clean(path, self.entries[path], False)
            records.append({
                "action": "reread",
                "path": path,
                "old": self.db.tags[path],
                "new": tags,
            })
        for path in missing:
            records.append({
                "action": "remove",
                "path": path,
                "tags": self.db.tags[path],
                "stats": list(self.db.stats.get(path, ())),
            })
        journal = self.db.journal("scan", records)
        if new_entries:
            self.db.add_files(new_entries)
        if changed:
            self.db.reread_files({
                p: (
                    self.entries[p],
                    library_scan.clean(p, self.entries[p], False)[0],
                )
                for p in changed
            })
        if missing:
            self.db.remove_files(missing)
        self.added = list(new_entries)
        bad = [p for p in new if p not in self.db.tags] + [
            p for p in missing if p in self.db.tags
        ]
        check = f" (CHECK FAILED for {len(bad)})" if bad else ""
        QMessageBox.information(
            self.parent(),
            "Scan for new files",
            f"Added {len(new)}, removed {len(missing)}, re-read {len(changed)}"
            f" files{check}.\n\nBackup: {backup}\nJournal: {journal}",
        )


class DuplicatesDialog(QDialog):
    # The new files that have the same artist and title as another file: tick
    # the groups to mark.

    def __init__(self, parent, db, groups: list[dict]):
        super().__init__(parent)
        self.db, self.groups = db, groups
        self.setWindowTitle("Duplicates among the new files")
        self.resize(900, 500)
        layout = QVBoxLayout(self)
        label = QLabel(
            "These files have the same artist and title as the new files. In"
            " each group the first file is kept as the primary (a FLAC;"
            " otherwise the newer, then the larger file) and the others are"
            " marked as its duplicates. The same song on different albums may"
            " be wanted in both places: untick such groups."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Album", "Role"])
        self.tree.setColumnWidth(0, 520)
        for group in groups:
            top = QTreeWidgetItem([
                (
                    f"{_first(db.tags[group['primary']], 'ARTIST')} -"
                    f" {_first(db.tags[group['primary']], 'TITLE')}"
                ),
                "",
                "",
            ])
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            top.setCheckState(0, Qt.CheckState.Checked)
            top.setData(0, Qt.ItemDataRole.UserRole, group)
            self.tree.addTopLevelItem(top)
            for path in [group["primary"]] + group["copies"]:
                role = (
                    "primary"
                    + (" (chosen at random)" if group["random"] else "")
                    if path == group["primary"]
                    else "duplicate"
                )
                top.addChild(
                    QTreeWidgetItem(
                        [path, _first(db.tags[path], "ALBUM"), role]
                    )
                )
            top.setExpanded(True)
        layout.addWidget(self.tree)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Mark ticked groups"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def exec_apply(self) -> None:
        if self.exec() != QDialog.DialogCode.Accepted:
            return
        marks: dict[str, str | None] = {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.checkState(0) == Qt.CheckState.Checked:
                marks.update(top.data(0, Qt.ItemDataRole.UserRole)["marks"])
        if not marks:
            return
        backup = self.db.backup("scan-duplicates")
        journal = self.db.journal(
            "scan",
            [
                {
                    "action": "duplicate",
                    "path": p,
                    "old": self.db.dup_of.get(p),
                    "new": q,
                }
                for p, q in marks.items()
            ],
        )
        self.db.set_duplicates(marks)
        QMessageBox.information(
            self.parent(),
            "Duplicates",
            f"Marked {len(marks)} files.\n\nBackup: {backup}\nJournal:"
            f" {journal}",
        )


def _first(tags: dict, key: str) -> str:
    return next((v for v in tags.get(key, []) if v.strip()), "")
