# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Standalone editor for the tags inside media files (no database).
from __future__ import annotations

import sys

# a program of its own: it leaves no .pyc files behind
sys.dont_write_bytecode = True

import os

from PyQt6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QKeySequence, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import tagio

from tag_editor import KEY_ROLE, PATH_ROLE, SEPARATOR, VALUE_ROLE, TagEditor


class Scanner(QThread):
    found = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = root

    def run(self) -> None:
        for path in tagio.find_media(self.root):
            if self.isInterruptionRequested():
                return
            try:
                self.found.emit(path, tagio.read_tags(path))
            except tagio.TagError:
                self.failed.emit(path)


# --------------------------------------------------------------------- main
# window

TREE_COLUMNS = [
    ("Name", None),
    ("Title", "TITLE"),
    ("Artist", "ARTIST"),
    ("Album", "ALBUM"),
    ("Track", "TRACKNUMBER"),
]


def _inside(path: str, root: str) -> bool:
    return os.path.commonpath([path, root]) == root


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1250, 720)
        self.roots: list[str] = []
        self.scanners: list[Scanner] = []
        self.tags: dict[str, dict[str, list[str]]] = {}
        self.cells: dict[str, list[QStandardItem]] = {}
        self.dirs: dict[str, QStandardItem] = {}
        self.unreadable = 0
        self.current: list[str] = []
        self._quiet = False

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
        self.tree.selectionModel().selectionChanged.connect(
            self._selection_changed
        )
        self._last_selection = QItemSelection()

        self.overview = QTreeWidget()
        self.overview.setHeaderLabels(["Tag / value", "Files"])
        self.overview.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.overview.setUniformRowHeights(True)
        self.overview.setColumnWidth(0, 380)
        self.overview.itemExpanded.connect(self._overview_expanded)
        self.overview.itemSelectionChanged.connect(self._overview_selected)
        self.overview.itemChanged.connect(self._overview_edited)
        self.overview.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.overview.customContextMenuRequested.connect(self._overview_menu)
        self.overview_filter = ""  # lower-case search text; "" shows everything
        self.overview_index: dict[str, dict[str, list[str]]] = {}
        self.overview_stale = True
        self.overview_timer = QTimer(self)
        self.overview_timer.setSingleShot(True)
        self.overview_timer.setInterval(400)
        self.overview_timer.timeout.connect(self._refresh_overview)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.tree, "Files")
        self.tabs.addTab(self.overview, "Tags")
        self.tabs.currentChanged.connect(lambda _: self._refresh_overview())

        self.summary = QLabel("No files selected")
        self.editor = TagEditor()
        self.editor.changed.connect(self._editor_changed)
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

        splitter = QSplitter()
        splitter.addWidget(self.tabs)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

        self._make_actions()
        self._editor_changed()

    def _make_actions(self) -> None:
        def action(text, shortcut, slot):
            a = QAction(text, self)
            a.setShortcut(shortcut)
            a.triggered.connect(slot)
            return a

        self.save_action = action(
            "&Save Tags", QKeySequence.StandardKey.Save, self.save
        )
        menu = self.menuBar().addMenu("&File")
        menu.addAction(
            action(
                "&Open Folder...",
                QKeySequence.StandardKey.Open,
                self.open_folder,
            )
        )
        menu.addAction(
            action(
                "&Add Folder...",
                "Ctrl+Shift+O",
                lambda: self.open_folder(add=True),
            )
        )
        menu.addAction(
            action("&Rescan", QKeySequence.StandardKey.Refresh, self.rescan)
        )
        menu.addSeparator()
        menu.addAction(self.save_action)
        menu.addSeparator()
        menu.addAction(
            action("&Quit", QKeySequence.StandardKey.Quit, self.close)
        )

    # -- scanning ----------------------------------------------------------

    def open_folder(self, add: bool = False) -> None:
        start = self.roots[-1] if self.roots else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        if path:
            self.scan([path], replace=not add)

    def rescan(self) -> None:
        if self.roots:
            self.scan(list(self.roots), replace=True)

    def scan(self, paths: list[str], replace: bool) -> None:
        if replace:
            if not self._confirm_discard():
                return
            self._stop_scanners()
            self._clear()
        for path in paths:
            root = os.path.abspath(path)
            if not os.path.isdir(root):
                self.statusBar().showMessage(f"Not a folder: {root}", 5000)
                continue
            if any(_inside(root, r) or _inside(r, root) for r in self.roots):
                self.statusBar().showMessage(
                    f"Already covered by an open folder: {root}", 5000
                )
                continue
            self.roots.append(root)
            scanner = Scanner(root, self)
            scanner.found.connect(self._file_found)
            scanner.failed.connect(lambda _p: self._count_unreadable())
            scanner.finished.connect(lambda s=scanner: self._scan_finished(s))
            self.scanners.append(scanner)
            self.tree.setSortingEnabled(False)
            scanner.start()
        self._update_status()

    def _stop_scanners(self) -> None:
        for s in self.scanners:
            s.requestInterruption()
        for s in self.scanners:
            s.wait()
        self.scanners.clear()

    def _clear(self) -> None:
        self.model.removeRows(0, self.model.rowCount())
        self.roots.clear()
        self.tags.clear()
        self.cells.clear()
        self.dirs.clear()
        self.unreadable = 0
        self._last_selection = QItemSelection()
        self.current = []
        self.editor.set_files([])
        self._update_summary()
        self._overview_changed()

    def _scan_finished(self, scanner: Scanner) -> None:
        if scanner in self.scanners:
            self.scanners.remove(scanner)
        if not self.scanners:
            self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
            self.tree.setSortingEnabled(True)
            self.tree.expandAll()
        self._update_status()

    def _count_unreadable(self) -> None:
        self.unreadable += 1

    def _update_status(self) -> None:
        text = f"{len(self.tags)} media files"
        if self.scanners:
            text = "Scanning... " + text
        if self.unreadable:
            text += f" ({self.unreadable} could not be read)"
        self.statusBar().showMessage(text)

    def _dir_item(self, path: str) -> QStandardItem:
        item = self.dirs.get(path)
        if item is None:
            is_root = path in self.roots
            item = QStandardItem(path if is_root else os.path.basename(path))
            item.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            )
            item.setData(None, PATH_ROLE)
            parent = (
                self.model.invisibleRootItem()
                if is_root
                else self._dir_item(os.path.dirname(path))
            )
            parent.appendRow(
                [item] + [QStandardItem() for _ in TREE_COLUMNS[1:]]
            )
            self.dirs[path] = item
            self.tree.expand(item.index())
        return item

    def _file_found(self, path: str, tags: dict) -> None:
        if path in self.tags:
            return
        self.tags[path] = tags
        name = QStandardItem(os.path.basename(path))
        name.setData(path, PATH_ROLE)
        name.setToolTip(path)
        name.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        )
        cells = [name] + [QStandardItem() for _ in TREE_COLUMNS[1:]]
        self.cells[path] = cells
        self._fill_cells(path)
        self._dir_item(os.path.dirname(path)).appendRow(cells)
        self._overview_changed()
        if len(self.tags) % 50 == 0:
            self._update_status()

    def _fill_cells(self, path: str) -> None:
        tags = self.tags[path]
        for cell, (_, key) in zip(self.cells[path][1:], TREE_COLUMNS[1:]):
            cell.setText(SEPARATOR.join(tags.get(key, [])))

    # -- selection ---------------------------------------------------------

    def _files_under(self, item: QStandardItem, seen: set, out: list) -> None:
        path = item.data(PATH_ROLE)
        if path:
            if path not in seen:
                seen.add(path)
                out.append(path)
            return
        for r in range(item.rowCount()):
            self._files_under(item.child(r, 0), seen, out)

    def _selected_files(self) -> list[str]:
        seen: set = set()
        out: list = []
        for index in self.tree.selectionModel().selectedRows(0):
            self._files_under(self.model.itemFromIndex(index), seen, out)
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
        self.editor.set_files([self.tags[p] for p in self.current])
        self._update_summary()

    def _update_summary(self) -> None:
        n = len(self.current)
        self.summary.setText(
            "No files selected"
            if n == 0
            else f"Editing {n} file{'s' if n != 1 else ''}"
            + (f": {os.path.basename(self.current[0])}" if n == 1 else "")
        )

    # -- tag overview ------------------------------------------------------

    def _overview_changed(self) -> None:
        self.overview_stale = True
        if self.tabs.currentWidget() is self.overview:
            self.overview_timer.start()

    def _refresh_overview(self) -> None:
        # Rebuild the Tags tab: every tag name in the library and its values.
        if (
            self.tabs.currentWidget() is not self.overview
            or not self.overview_stale
        ):
            return
        self.overview_stale = False
        index: dict[str, dict[str, list[str]]] = {}
        for path, tags in self.tags.items():
            for key, values in tags.items():
                by_value = index.setdefault(key, {})
                for value in set(values):
                    by_value.setdefault(value, []).append(path)
        self.overview_index = index
        expanded = {
            self.overview.topLevelItem(i).text(0)
            for i in range(self.overview.topLevelItemCount())
            if self.overview.topLevelItem(i).isExpanded()
        }
        self.overview.blockSignals(True)
        self.overview.clear()
        for key in sorted(index, key=tagio.sort_key):
            values = self._visible_values(key)
            if not values:
                continue
            paths = {p for ps in values.values() for p in ps}
            if self.overview_filter and self.overview_filter not in key.lower():
                expanded.add(key)  # matched on a value: show which
            item = QTreeWidgetItem([key, str(len(paths))])
            item.setData(0, PATH_ROLE, sorted(paths))
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
            )
            self.overview.addTopLevelItem(item)
        self.overview.blockSignals(False)
        for i in range(self.overview.topLevelItemCount()):
            if self.overview.topLevelItem(i).text(0) in expanded:
                self.overview.topLevelItem(i).setExpanded(True)

    def _visible_values(self, key: str) -> dict[str, list[str]]:
        # The values of a tag that pass the search; all of them if the tag name
        # matches.
        values = self.overview_index.get(key, {})
        needle = self.overview_filter
        if not needle or needle in key.lower():
            return values
        return {v: ps for v, ps in values.items() if needle in v.lower()}

    def _overview_menu(self, pos) -> None:
        menu = QMenu(self.overview)
        search = menu.addAction("Search...")
        if menu.exec(self.overview.viewport().mapToGlobal(pos)) is search:
            self._search_overview()

    def _search_overview(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Search tags",
            "Show tags and values containing\n"
            "(leave empty to show everything):",
            text=self.overview_filter,
        )
        if not ok:
            return
        text = text.strip()
        self.overview_filter = text.lower()
        self.overview.headerItem().setText(
            0, f'Tag / value   [search: "{text}"]' if text else "Tag / value"
        )
        self.overview_stale = True
        self._refresh_overview()

    def _overview_expanded(self, item: QTreeWidgetItem) -> None:
        if item.childCount():
            return
        self.overview.blockSignals(True)
        for value, paths in sorted(
            self._visible_values(item.text(0)).items(),
            key=lambda kv: kv[0].lower(),
        ):
            child = QTreeWidgetItem([value, str(len(paths))])
            child.setData(0, PATH_ROLE, sorted(paths))
            child.setData(0, KEY_ROLE, item.text(0))
            child.setData(0, VALUE_ROLE, value)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsEditable)
            item.addChild(child)
        self.overview.blockSignals(False)

    def _overview_edited(self, item: QTreeWidgetItem, column: int) -> None:
        # A value was renamed in the Tags tab: change it in every file that has
        # it.
        key, old = item.data(0, KEY_ROLE), item.data(0, VALUE_ROLE)
        if column != 0 or key is None:
            return
        new = item.text(0).strip()
        paths = list(item.data(0, PATH_ROLE))
        if new == old:
            return
        if (
            not new
            or not self._confirm_discard()
            or QMessageBox.question(
                self,
                "Change value",
                f"Change {key} '{old}' to '{new}' in {len(paths)} file(s)?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            self.overview.blockSignals(True)
            item.setText(0, old)
            self.overview.blockSignals(False)
            return

        def changes(path: str) -> dict:
            values = [new if v == old else v for v in self.tags[path][key]]
            return {
                key: list(dict.fromkeys(values))
            }  # merging into an existing value drops duplicates

        self._write_files(paths, changes)
        self.current = [p for p in self.current if p in self.tags]
        self.editor.set_files([self.tags[p] for p in self.current])
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

    # -- saving ------------------------------------------------------------

    def _editor_changed(self) -> None:
        dirty = bool(self.editor.pending_changes())
        self.save_button.setEnabled(dirty)
        self.save_action.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)
        self.setWindowTitle("Media Tag Editor" + (" *" if dirty else ""))

    def save(self) -> bool:
        changes = self.editor.pending_changes()
        if not changes:
            return True
        paths = list(self.current)
        ok = self._write_files(paths, lambda _path: changes)
        self.editor.set_files([self.tags[p] for p in paths])
        return ok

    def _write_files(self, paths: list[str], changes_for) -> bool:
        # Write changes_for(path) to each file; report failures. True if all
        # succeeded.
        errors: list[str] = []
        progress = QProgressDialog(
            "Saving tags...", "Cancel", 0, len(paths), self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(500)
        cancelled = False
        for i, path in enumerate(paths):
            progress.setValue(i)
            if progress.wasCanceled():
                cancelled = True
                break
            try:
                self.tags[path] = tagio.write_tags(path, changes_for(path))
                self._fill_cells(path)
            except tagio.TagError as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        progress.setValue(len(paths))
        self._overview_changed()
        if errors or cancelled:
            shown = errors[:20] + (
                [f"... and {len(errors) - 20} more"] if len(errors) > 20 else []
            )
            box = QMessageBox(
                QMessageBox.Icon.Warning,
                "Save incomplete",
                ("Saving was cancelled. " if cancelled else "")
                + (
                    f"{len(errors)} file(s) could not be saved."
                    if errors
                    else ""
                ),
                parent=self,
            )
            box.setDetailedText("\n".join(shown))
            box.exec()
        return not errors and not cancelled

    def _confirm_discard(self) -> bool:
        # True if it is fine to replace what the editor shows.
        if not self.editor.pending_changes():
            return True
        box = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save the changes to the selected files?",
            box.Save | box.Discard | box.Cancel,
            box.Save,
        )
        if answer == box.Save:
            return self.save()
        return answer == box.Discard

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self._stop_scanners()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.scan(sys.argv[1:], replace=True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
