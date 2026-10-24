# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Adds one value to a tag (a genre, say) in every selected file, or removes
# one, leaving each file's other values and tags alone.  Shared by the editor
# and the player.
from __future__ import annotations

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from tagdb import SPLIT_TAGS


def change_value(parent, db, paths, mode, key, choices, write) -> bool:
    # mode: "add" or "remove".  choices: the values to offer (may be empty).
    # write(paths, changes_for) stores the change; True if something was
    # changed.
    if mode == "add":
        if choices:
            text, ok = QInputDialog.getItem(
                parent,
                f"Add a value to {key}",
                f"Value to add to {key} in {len(paths)} file(s):",
                choices,
                0,
                True,
            )
            text = text if ok else ""
        else:
            text, ok = QInputDialog.getText(
                parent,
                f"Add a value to {key}",
                f"Value to add to {key} in {len(paths)} file(s):",
            )
        values = (
            (
                [v.strip() for v in text.split(";") if v.strip()]
                if key in SPLIT_TAGS or choices
                else [text.strip()]
            )
            if ok
            else []
        )
        values = [v for v in dict.fromkeys(values) if v]
        if not values:
            return False
        affected = [
            p
            for p in paths
            if any(v not in db.tags[p].get(key, []) for v in values)
        ]
        question = (
            f"Add {' + '.join(repr(v) for v in values)} to {key} in"
            f" {len(affected)} of {len(paths)} file(s)?"
        )

        def changes(path: str) -> dict:
            current = list(db.tags[path].get(key, []))
            return {key: current + [v for v in values if v not in current]}

    else:
        counts: dict[str, int] = {}
        for p in paths:
            for v in set(db.tags[p].get(key, [])):
                counts[v] = counts.get(v, 0) + 1
        if not counts:
            QMessageBox.information(
                parent,
                "Remove a value",
                f"None of the selected files has {key}.",
            )
            return False
        labels = [
            f"{v}   ({n} file{'s' if n != 1 else ''})"
            for v, n in sorted(counts.items(), key=lambda kv: kv[0].lower())
        ]
        label, ok = QInputDialog.getItem(
            parent,
            f"Remove a value from {key}",
            f"Value to remove from {key}:",
            labels,
            0,
            False,
        )
        if not ok:
            return False
        value = sorted(counts, key=str.lower)[labels.index(label)]
        affected = [
            p for p in paths if value in db.tags[p].get(key, [])
        ]
        question = (
            f"Remove {value!r} from {key} in {len(affected)} of"
            f" {len(paths)} file(s)?"
        )

        def changes(path: str) -> dict:
            return {
                key: [
                    v for v in db.tags[path].get(key, []) if v != value
                ]
            }

    if not affected:
        QMessageBox.information(
            parent,
            "Nothing to change",
            "Every selected file is already as asked.",
        )
        return False
    if (
        QMessageBox.question(parent, "Change tags", question)
        != QMessageBox.StandardButton.Yes
    ):
        return False
    write(affected, changes)
    return True
