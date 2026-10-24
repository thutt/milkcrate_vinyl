# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The "Detect BPM" and "Measure loudness" actions of the editor and the player:
# they work on the selected files, show progress with a Cancel button, store
# the results in the database only (file_analysis.py) and return the paths that
# changed so the caller can redraw them.
from __future__ import annotations

import os
import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

import file_analysis

CONFIRM_ABOVE = 500  # files; a run of this size takes minutes


def _jobs() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _confirmed(parent, title: str, count: int) -> bool:
    return (
        count <= CONFIRM_ABOVE
        or QMessageBox.question(
            parent,
            title,
            f"{count} files will be analysed.  This takes a while.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        == QMessageBox.StandardButton.Ok
    )


def _run(parent, title: str, function, db, paths: list[str]):
    root = db.root()
    if not root or not os.path.isdir(root):
        QMessageBox.warning(
            parent,
            title,
            f"The music folder is not available:\n{root or '(none recorded)'}",
        )
        return None
    box = QProgressDialog(f"{title}: 0 of {len(paths)} ...", "Cancel",
                          0, len(paths), parent)
    box.setWindowTitle(title)
    box.setWindowModality(Qt.WindowModality.WindowModal)
    box.setMinimumDuration(0)
    box.setValue(0)

    def tick(n: int) -> bool:
        box.setLabelText(f"{title}: {n} of {len(paths)} ...")
        box.setValue(n)
        QApplication.processEvents()
        return not box.wasCanceled()

    try:
        return file_analysis.run(function, root, paths, _jobs(), tick)
    finally:
        box.close()


def detect_bpm(parent, db, paths: list[str]) -> list[str]:
    # Files that already have a BPM are left alone.
    title = "Detect BPM"
    todo = [
        p for p in paths if (db.tags[p].get("BPM") or ["0"])[0] in ("", "0")
    ]
    have = len(paths) - len(todo)
    if not todo:
        QMessageBox.information(
            parent,
            title,
            f"All {len(paths)} selected file{'s' if len(paths) != 1 else ''}"
            " already have a BPM.",
        )
        return []
    if not _confirmed(parent, title, len(todo)):
        return []
    results = _run(parent, title, file_analysis.bpm_one, db, todo)
    if results is None:
        return []
    try:
        stored, low = file_analysis.store_bpm(db, results)
    except sqlite3.Error as e:
        QMessageBox.critical(parent, "Cannot save", str(e))
        return []
    text = f"BPM set for {len(stored)} of {len(todo)} files."
    if low:
        text += (
            f"\n{len(low)} could not be decided reliably and stay at 0:\n"
            + "\n".join(low[:10])
            + ("\n..." if len(low) > 10 else "")
        )
    if have:
        text += f"\n{have} already had a BPM and were not analysed."
    QMessageBox.information(parent, title, text)
    return stored


def measure_loudness(parent, db, paths: list[str]) -> list[str]:
    # Every selected file is measured again; the value replaces an old one.
    title = "Measure loudness"
    if not _confirmed(parent, title, len(paths)):
        return []
    results = _run(parent, title, file_analysis.loudness_one, db, paths)
    if results is None:
        return []
    try:
        done, failed = file_analysis.store_loudness(db, results)
    except sqlite3.Error as e:
        QMessageBox.critical(parent, "Cannot save", str(e))
        return []
    text = f"Loudness set for {len(done)} of {len(paths)} files."
    if failed:
        text += (
            f"\n{len(failed)} could not be measured:\n"
            + "\n".join(failed[:10])
            + ("\n..." if len(failed) > 10 else "")
        )
    QMessageBox.information(parent, title, text)
    return done
