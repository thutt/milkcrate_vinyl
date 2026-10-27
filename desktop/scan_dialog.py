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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QProgressDialog,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
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
            f"Nothing to do: all {result.unchanged} files match the database."
            + (
                f"\n\n{len(result.ignored)} ignored files were skipped"
                " (File > Ignored Scan Files)."
                if result.ignored
                else ""
            ),
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


GROUPS = (
    ("add", "Add to database"),
    ("ignore", "Ignore in future"),
    ("delete", "Delete from disk"),
)
PATH_ROLE = Qt.ItemDataRole.UserRole
TEXT_ROLE = Qt.ItemDataRole.UserRole + 1  # lower case text, for the filter


def _file_tree(headers: list[str]) -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderLabels(headers)
    tree.setRootIsDecorated(False)
    tree.setColumnWidth(0, 340)
    tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
    tree.setSortingEnabled(True)
    tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    return tree


def _filter_tree(tree: QTreeWidget, text: str) -> None:
    words = text.lower().split()
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        hay = item.data(0, TEXT_ROLE)
        item.setHidden(not all(w in hay for w in words))


class FoundPage(QWidget):
    # The new files, in two columns.  Left: the files found and not yet
    # decided on.  Right: what to do with the files moved there.  Nothing is
    # done until the dialog is applied.
    changed = pyqtSignal()

    def __init__(self, cells, paths: list[str]):
        super().__init__()
        self.cells = cells  # cells(path) -> the text of the row's columns
        headers = ["File", "Title", "Artist", "Album"]
        self.found = _file_tree(headers)
        self.groups = {key: _file_tree(headers) for key, _t in GROUPS}
        self.found_label = QLabel()
        self.group_labels = {key: QLabel() for key, _t in GROUPS}
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(
            "Filter the found files (words in the path, title, artist or"
            " album)"
        )
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(
            lambda text: _filter_tree(self.found, text)
        )
        left = QVBoxLayout()
        left.addWidget(self.found_label)
        left.addWidget(self.filter)
        left.addWidget(self.found)
        middle = QVBoxLayout()
        middle.addStretch()
        for key, title in GROUPS:
            button = QPushButton(f"{title}  ▸")
            button.setToolTip(
                "Move the selected files (the shown ones) to the list on the"
                " right"
            )
            button.clicked.connect(lambda _=False, k=key: self._move_to(k))
            middle.addWidget(button)
        back = QPushButton("◂  Put back")
        back.setToolTip("Move the selected files on the right back")
        back.clicked.connect(self._put_back)
        middle.addWidget(back)
        middle.addStretch()
        right = QVBoxLayout()
        for key, _title in GROUPS:
            right.addWidget(self.group_labels[key])
            right.addWidget(self.groups[key])
        row = QHBoxLayout(self)
        row.addLayout(left, 3)
        row.addLayout(middle)
        row.addLayout(right, 2)
        self.found.setSortingEnabled(False)
        for path in paths:
            self._add(self.found, path)
        self.found.setSortingEnabled(True)
        self._count()

    def _add(self, tree: QTreeWidget, path: str) -> None:
        cells = self.cells(path)
        item = QTreeWidgetItem(cells)
        item.setData(0, PATH_ROLE, path)
        item.setData(0, TEXT_ROLE, " ".join([path] + cells).lower())
        tree.addTopLevelItem(item)

    def add_found(self, path: str) -> None:
        self._add(self.found, path)
        _filter_tree(self.found, self.filter.text())
        self._count()

    def _take(self, tree: QTreeWidget) -> list[str]:
        paths = []
        for item in tree.selectedItems():
            if not item.isHidden():
                paths.append(item.data(0, PATH_ROLE))
                tree.invisibleRootItem().removeChild(item)
        return paths

    def _move(self, source: QTreeWidget, target: QTreeWidget) -> None:
        target.setSortingEnabled(False)
        for path in self._take(source):
            self._add(target, path)
        target.setSortingEnabled(True)
        _filter_tree(self.found, self.filter.text())
        self._count()

    def _move_to(self, key: str) -> None:
        self._move(self.found, self.groups[key])

    def _put_back(self) -> None:
        for tree in self.groups.values():
            self._move(tree, self.found)

    def _count(self) -> None:
        self.found_label.setText(
            f"Found, not decided yet ({self.found.topLevelItemCount()})"
        )
        for key, title in GROUPS:
            self.group_labels[key].setText(
                f"{title} ({self.groups[key].topLevelItemCount()})"
            )
        self.changed.emit()

    def refresh(self) -> None:
        # The text of the rows changed (the ALBUM option).
        for tree in [self.found] + list(self.groups.values()):
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                path = item.data(0, PATH_ROLE)
                cells = self.cells(path)
                for column, text in enumerate(cells):
                    item.setText(column, text)
                item.setData(0, TEXT_ROLE, " ".join([path] + cells).lower())

    def paths(self, key: str) -> list[str]:
        tree = self.groups[key]
        return [
            tree.topLevelItem(i).data(0, PATH_ROLE)
            for i in range(tree.topLevelItemCount())
        ]


class IgnoredPage(QWidget):
    # The files that scans skip.  "Stop ignoring" takes files off the list
    # (when the dialog is applied).
    removed = pyqtSignal(list)

    def __init__(self, paths: list[str]):
        super().__init__()
        self.tree = _file_tree(["Ignored file"])
        self.tree.setColumnWidth(0, 600)
        self.label = QLabel()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter the ignored files")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(
            lambda text: _filter_tree(self.tree, text)
        )
        self.tree.setSortingEnabled(False)
        for path in paths:
            item = QTreeWidgetItem([path])
            item.setData(0, PATH_ROLE, path)
            item.setData(0, TEXT_ROLE, path.lower())
            self.tree.addTopLevelItem(item)
        self.tree.setSortingEnabled(True)
        stop = QPushButton("Stop ignoring selected files")
        stop.clicked.connect(self._stop)
        box = QVBoxLayout(self)
        note = QLabel(
            "Scans do not list these files. Select files and stop ignoring"
            " them to have a scan list them again."
        )
        note.setWordWrap(True)
        box.addWidget(note)
        box.addWidget(self.label)
        box.addWidget(self.filter)
        box.addWidget(self.tree)
        box.addWidget(stop)
        self._count()

    def _stop(self) -> None:
        gone = []
        for item in self.tree.selectedItems():
            if not item.isHidden():
                gone.append(item.data(0, PATH_ROLE))
                self.tree.invisibleRootItem().removeChild(item)
        self._count()
        if gone:
            self.removed.emit(gone)

    def _count(self) -> None:
        self.label.setText(f"Ignored files ({self.tree.topLevelItemCount()})")

    def paths(self) -> list[str]:
        return [
            self.tree.topLevelItem(i).data(0, PATH_ROLE)
            for i in range(self.tree.topLevelItemCount())
        ]


class ScanDialog(QDialog):
    def __init__(
        self, parent, db, result: library_scan.Scan, entries: dict[str, dict]
    ):
        super().__init__(parent)
        self.setWindowTitle("Scan for new files")
        self.resize(1150, 650)
        self.db, self.result, self.entries = db, result, entries
        self.added: list[str] = []
        self.unignored: set[str] = set()
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
        self.found = FoundPage(self._cells, result.new)
        self.album.toggled.connect(lambda _: self.found.refresh())
        self.found.changed.connect(self._titles)
        new_page = QWidget()
        box = QVBoxLayout(new_page)
        note = QLabel(
            "Select files on the left (Shift or Ctrl-click, Ctrl+A) and move"
            " them to what should happen to them. Files left on the left are"
            " not touched and are listed again by the next scan. Nothing is"
            " done until you click Apply."
        )
        note.setWordWrap(True)
        box.addWidget(note)
        box.addWidget(self.album)
        box.addWidget(self.found)
        self.tabs.addTab(new_page, "")
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
                self.missing_tree,
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
                "These files changed on disk. Re-reading replaces the stored"
                " tags, so any edits made in the database are lost. Tick the"
                " ones to re-read.",
            ),
            "",
        )
        self.ignored = IgnoredPage(db.scan_ignored())
        self.ignored.removed.connect(self._stop_ignoring)
        self.tabs.addTab(self.ignored, "")
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

    def _cells(self, path: str) -> list[str]:
        tags, _notes = library_scan.clean(
            path, self.entries[path], self.album.isChecked()
        )
        error = self.entries[path].get("error")
        return [
            path,
            f"unreadable: {error}" if error else _first(tags, "TITLE"),
            _first(tags, "ARTIST"),
            _first(tags, "ALBUM"),
        ]

    def _stop_ignoring(self, paths: list[str]) -> None:
        # Files on disk that a scan skipped come back to the found list.
        self.unignored.update(paths)
        back = [p for p in paths if p in self.result.ignored]
        entries = library_scan.read_files(self.result.root, back)
        if entries:
            self.entries.update(entries)
            for path in back:
                self.found.add_found(path)

    def _tree(self, headers: list[str]) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels(headers)
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 420)
        tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        tree.itemChanged.connect(lambda *_: self._titles())
        tree.itemChanged.connect(
            lambda item, _c, t=tree: self._shift_click(t, item)
        )
        return tree

    def _page(self, tree: QTreeWidget, note: str):
        page = QWidget()
        box = QVBoxLayout(page)
        label = QLabel(note + " Shift-click a tick box to tick or untick all.")
        label.setWordWrap(True)
        box.addWidget(label)
        box.addWidget(tree)
        return page

    def _shift_click(self, tree: QTreeWidget, item: QTreeWidgetItem) -> None:
        # Shift-click on a tick box sets every row to the state that click
        # gave the box, so repeating it turns all on, then all off.
        if not (
            QApplication.keyboardModifiers()
            & Qt.KeyboardModifier.ShiftModifier
        ):
            return
        state = item.checkState(0)
        tree.blockSignals(True)
        for i in range(tree.topLevelItemCount()):
            tree.topLevelItem(i).setCheckState(0, state)
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

    @staticmethod
    def _checked(tree: QTreeWidget) -> list[str]:
        return [
            tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked
        ]

    def _titles(self) -> None:
        decided = sum(len(self.found.paths(k)) for k, _t in GROUPS)
        self.tabs.setTabText(
            0,
            f"New ({self.found.found.topLevelItemCount() + decided},"
            f" {decided} decided)",
        )
        for n, (tree, name) in enumerate(
            (
                (self.missing_tree, "Missing"),
                (self.changed_tree, "Changed"),
            ),
            1,
        ):
            self.tabs.setTabText(
                n,
                f"{name} ({len(self._checked(tree))} of"
                f" {tree.topLevelItemCount()})",
            )
        self.tabs.setTabText(
            3, f"Ignored ({self.ignored.tree.topLevelItemCount()})"
        )

    def accept(self) -> None:
        doomed = self.found.paths("delete")
        if doomed:
            box = QMessageBox(
                QMessageBox.Icon.Warning,
                "Delete from disk",
                f"Delete {len(doomed)} file{'s' if len(doomed) != 1 else ''}"
                " from the disk, and the folders that become empty?\n\n"
                + "\n".join(doomed[:8])
                + ("\n..." if len(doomed) > 8 else "")
                + "\n\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                self,
            )
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        super().accept()

    def apply(self) -> None:
        # Back up, then add, remove and re-read the chosen files, writing a
        # journal line for each; then ignore and delete.
        new = self.found.paths("add")
        ignore = self.found.paths("ignore")
        doomed = self.found.paths("delete")
        missing, changed = (
            self._checked(t) for t in (self.missing_tree, self.changed_tree)
        )
        kept = [p for p in self.ignored.paths() if p not in self.unignored]
        ignored = sorted(set(kept + ignore))
        if ignored != self.db.scan_ignored():
            self.db.set_scan_ignored(ignored)
        backup = journal = ""
        records = [{"action": "ignore", "path": p} for p in ignore] + [
            {"action": "delete", "path": p} for p in doomed
        ]
        failed, folders = (
            self.db.delete_from_disk(doomed) if doomed else ([], [])
        )
        if not (new or missing or changed):
            if records:
                journal = self.db.journal("scan", records)
                self._report(
                    0, 0, 0, len(ignore), len(doomed), failed, "", journal, ""
                )
            return
        backup = self.db.backup("scan")
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
        self._report(
            len(new),
            len(missing),
            len(changed),
            len(ignore),
            len(doomed),
            failed,
            backup,
            journal,
            check,
        )

    def _report(
        self, added, removed, reread, ignore, doomed, failed, backup, journal,
        check,
    ) -> None:
        text = (
            f"Added {added}, removed {removed}, re-read {reread} files"
            f"{check}; ignored {ignore}, deleted {doomed - len(failed)} from"
            " the disk."
        )
        if failed:
            text += (
                f"\n{len(failed)} could not be deleted:\n"
                + "\n".join(failed[:10])
                + ("\n..." if len(failed) > 10 else "")
            )
        if backup:
            text += f"\n\nBackup: {backup}"
        QMessageBox.information(
            self.parent(),
            "Scan for new files",
            f"{text}\nJournal: {journal}",
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


def edit_ignored(parent, db) -> None:
    # File > Ignored Scan Files: show the ignored files and stop ignoring some.
    dialog = QDialog(parent)
    dialog.setWindowTitle("Ignored scan files")
    dialog.resize(800, 500)
    layout = QVBoxLayout(dialog)
    page = IgnoredPage(db.scan_ignored())
    layout.addWidget(page)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        db.set_scan_ignored(page.paths())


def _first(tags: dict, key: str) -> str:
    return next((v for v in tags.get(key, []) if v.strip()), "")
