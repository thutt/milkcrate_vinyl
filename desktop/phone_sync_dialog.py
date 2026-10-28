# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The File > Sync with Phone dialog shared by the player and the editor.
#
# The phone is found on the local network (Wi-Fi) by a broadcast search; the
# pairing code shown in its app (Settings > Sync with the desktop > Start) is
# entered once; the last five codes are remembered (one per phone) and can be
# picked from a list.  The
# phone's database is merged into the program's own database (newer write per
# track wins, plays on dynamic playlists are
# replayed), the result is sent back, then new music is copied.  Everything but
# the merge runs in a thread.
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading

from PyQt6.QtCore import QSettings, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

import phone_files
import phone_link
import phone_sync

ATTEMPTS = 3
LIVE: set = set()  # stopped jobs still ending


class Job(QThread):
    # Runs fn(log) in a thread; log(text) adds a line to the dialog.
    line = pyqtSignal(str)
    progress = pyqtSignal(int, int, float, float, str, float, float)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        try:
            self.succeeded.emit(self.fn(self.line.emit))
        except Exception as e:  # reported in the dialog
            self.failed.emit(e)


class SyncDialog(QDialog):
    def __init__(self, parent, db, synced, confirm=lambda: True):
        # db: the program's TagDb.  synced(report) is called after the merge, so
        # the program can show the changes.
        super().__init__(parent)
        self.db, self.synced, self.job = db, synced, None
        self.stop = threading.Event()  # set when Close asks the job to stop
        self.setWindowTitle("Sync with Phone")
        self.resize(640, 460)
        self.settings = QSettings("music-tags", "phone-sync")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "On the phone open the app, Settings, Sync with the desktop,"
                " Start.  The phone and this computer must be on the same"
                " Wi-Fi.\nThe pairing code the phone shows is entered once; the"
                " address is only needed when the phone is not found by itself."
            )
        )
        form = QFormLayout()
        self.code = (
            QComboBox()
            # the codes of the last phones synced, the newest first; new ones
            # can be
            # typed
        )
        self.code.setEditable(True)
        self.code.lineEdit().setPlaceholderText(
            "pairing code shown on the phone"
        )
        self.code.lineEdit().setMaxLength(48)
        self.code.setToolTip(
            "The last five codes are remembered, one per phone: pick the phone"
            " to sync with, or type a new code"
        )
        self._fill_codes()
        self.host = QLineEdit()
        self.host.setPlaceholderText(
            "automatic (leave empty unless the phone is not found)"
        )
        form.addRow("Pairing code", self.code)
        form.addRow("Phone address", self.host)
        layout.addLayout(form)
        self.music = QCheckBox("Also copy new music to the phone")
        self.music.setChecked(self.settings.value("music", True, bool))
        layout.addWidget(self.music)
        self.log = QPlainTextEdit(readOnly=True)
        layout.addWidget(self.log, 1)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        self.status = QLabel()  # what is happening now
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.go = QPushButton("Sync")
        self.go.setDefault(True)
        self.go.clicked.connect(self.start)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(self.go)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.link = None
        self.tmp = ""

    def _recent(self) -> list[dict]:
        try:
            recent = json.loads(self.settings.value("recent_codes", "[]", str))
            if isinstance(recent, list):
                return [
                    r for r in recent if isinstance(r, dict) and r.get("code")
                ]
        except ValueError:
            pass
        old = self.settings.value(
            "code", "", str
        )  # a version that kept only one code
        return [{"code": old, "name": ""}] if old else []

    def _fill_codes(self) -> None:
        self.code.clear()
        for r in self._recent():
            self.code.addItem(
                f"{r['code']}   {r.get('name', '')}".rstrip(), r["code"]
            )
        self.code.setCurrentIndex(0 if self.code.count() else -1)

    def _chosen_code(self) -> str:
        # The code picked from the list, or typed (a picked entry reads 'code
        # name': the first word is the code).
        words = self.code.currentText().split()
        return words[0] if words else ""

    def _remember_code(self, code: str, name: str) -> None:
        recent = phone_link.remember(self._recent(), code, name)
        self.settings.setValue("recent_codes", json.dumps(recent))
        self._fill_codes()

    def reject(self) -> None:
        self.close()  # Escape must act as Close

    def closeEvent(self, event) -> None:
        # Close stops the transfer and closes at once: the requests in use
        # are broken off, so a phone that went to sleep cannot keep the
        # dialog open; the thread ends by itself and removes the work files.
        if self.job is not None and self.job.isRunning():
            self.stop.set()
            if self.link is not None:
                self.link.abort()
            LIVE.add(self.job)  # the thread must outlive the dialog
            self.job.setParent(None)
        event.accept()

    def ended(self) -> None:
        # The job finished after Close stopped it.
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = ""
        LIVE.discard(self.job)

    def next_step(self, then, result) -> None:
        if self.stop.is_set():
            self.ended()
        else:
            then(result)

    def say(self, text: str) -> None:
        self.log.appendPlainText(text)
        if not self.bar.isVisible():  # while copying, the progress is shown
            self.status.setText(text)

    def show_progress(
        self, done, total, done_bytes, total_bytes, current, rate, minutes
    ) -> None:
        self.bar.setVisible(True)
        self.bar.setMaximum(max(int(total_bytes / 1e6), 1))
        self.bar.setValue(int(done_bytes / 1e6))
        self.bar.setFormat(f"{done} of {total} files")
        left = (
            "less than a minute"
            if minutes < 1
            else f"about {minutes:.0f} min"
        )
        self.status.setText(
            f"Copying {current}\n{done_bytes / 1e9:.2f} of"
            f" {total_bytes / 1e9:.2f} GB, {rate:.0f} MB/s, {left} left"
        )

    def busy(self, on: bool) -> None:
        self.go.setEnabled(not on)
        self.music.setEnabled(not on)
        self.code.setEnabled(not on)
        self.host.setEnabled(not on)

    def run_job(self, fn, then, on_fail=None) -> None:
        self.job = Job(fn, self)
        self.job.line.connect(self.say)
        self.job.progress.connect(self.show_progress)
        failed = on_fail or self.fail
        self.job.succeeded.connect(lambda r: self.next_step(then, r))
        self.job.failed.connect(lambda e: self.next_step(failed, e))
        self.job.start()

    def fail(self, error) -> None:
        self.say(f"Stopped: {error}")
        self.finish()

    def finish(self) -> None:
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = ""
        self.busy(False)
        self.bar.setVisible(False)

    def completed(self, ok: bool) -> None:
        # Everything is done.  The dialog closes (the program's status bar says
        # what changed); with failures it stays open.
        self.finish()
        if self.job is not None:
            self.job.wait()  # the thread has emitted its result and is ending
        if not ok:
            self.say("Done, but some files failed (listed above).")
            return
        parent = self.parent()
        if (
            parent is not None
            and hasattr(parent, "statusBar")
            and self.report is not None
        ):
            parent.statusBar().showMessage(
                "Phone sync done: " + self.report.text(), 15000
            )
        self.accept()

    # -- the steps ---------------------------------------------------------

    def start(self) -> None:
        code, host = self._chosen_code(), self.host.text().strip()
        if not code:
            self.say("Enter the pairing code the phone shows.")
            return
        self.settings.setValue("music", self.music.isChecked())
        self.busy(True)
        self.tmp = tempfile.mkdtemp(prefix="phone-sync-")
        self.attempt = 0
        self.report = None

        def connect(log):
            link = phone_link.find_phone(code, host or None)
            info = link.hello()
            log(
                f"Phone {info.get('name', '')} found at {link.host}"
                f" ({info['free'] / 1e9:.1f} GB free on the card)."
            )
            return link

        self.run_job(connect, self.connected)

    def connected(self, link) -> None:
        self.link = link
        self._remember_code(
            link.code, link.info.get("name", "")
        )  # the phone accepted it: keep it among the last five
        self.fetch()

    def fetch(self) -> None:
        self.attempt += 1
        pulled = os.path.join(self.tmp, "phone.db")

        def pull(log):
            log("Copying the phone's database...")
            try:
                return pulled, self.link.get_database(pulled)
            except phone_link.NoDatabase:
                log("The phone has no database yet; it gets this one.")
                return None, 0

        self.run_job(pull, self.pulled)

    def pulled(self, result) -> None:
        pulled, generation = result
        self.generation = generation
        try:
            backup = (
                self.db.path + ".bak-before-phone-sync"
                if self.attempt == 1
                else None
            )
            snapshot, report = phone_sync.merge_and_snapshot(
                self.db, pulled, self.tmp, backup
            )
        except sqlite3.Error as e:
            self.fail(f"cannot merge: {e}")
            return
        if report.problems:
            self.fail(report.text())
            return
        if pulled:
            self.say("From the phone: " + report.text())
        if self.report is None:
            self.report = report
        else:  # a second round: keep what the first one found
            self.report.paths += report.paths
            self.report.changes += report.changes
            self.report.playlists_changed |= report.playlists_changed

        def push(log):
            log("Sending the merged database...")
            self.link.put_database(snapshot, generation)

        self.run_job(push, self.pushed, self.push_failed)

    def push_failed(self, error) -> None:
        if isinstance(error, phone_link.Conflict) and self.attempt < ATTEMPTS:
            self.say(
                "The phone recorded a play meanwhile; fetching its database"
                " again."
            )
            self.fetch()
            return
        self.fail(error)

    def pushed(self, _result) -> None:
        self.say("The phone has the merged database.")
        if self.report is not None:
            self.synced(self.report)
        if not self.music.isChecked():
            self.run_job(
                lambda log: self.link.done(), lambda _r: self.completed(True)
            )
            return
        db_path = self.db.path
        link = self.link

        def copy(log):
            root = (
                sqlite3.connect(db_path)
                .execute("SELECT value FROM meta WHERE key = 'root'")
                .fetchone()[0]
            )
            log(
                "Listing the library and the files on the card (a minute or"
                " more for a large library)..."
            )
            files, skipped = phone_files.manifest(db_path, root)
            have = {k.lower(): v for k, v in link.files().items()}
            todo = [f for f in files if have.get(f[1].lower()) != f[2]]
            log(
                f"{len(files)} files in the library, {len(files) - len(todo)}"
                f" already on the phone, {len(todo)} to copy"
                f" ({sum(s for _f, _r, s in todo) / 1e9:.1f} GB)."
            )
            failed = (
                phone_files.copy_files(
                    todo,
                    lambda source, rel: link.put_file(source, rel),
                    2,
                    log,
                    self.stop.is_set,
                    self.job.progress.emit,
                )
                if todo
                else 0
            )
            if not self.stop.is_set():
                # the track samples go into the Ringtones folder as well
                failed = (
                    phone_files.copy_ringtones(
                        link,
                        files,
                        2,
                        log,
                        self.stop.is_set,
                        self.job.progress.emit,
                    )
                    or failed
                )
            if not self.stop.is_set():
                try:
                    link.done()
                except phone_link.LinkError:
                    pass  # the phone is gone; the next sync compares again
            return failed

        self.run_job(copy, lambda failed: self.completed(not failed))


def open_sync_dialog(parent, db, synced) -> None:
    SyncDialog(parent, db, synced).exec()
