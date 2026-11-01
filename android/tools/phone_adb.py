# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Runs adb commands and lists what is on the phone.
from __future__ import annotations

import os
import shutil
import subprocess


def adb_program() -> str:
    # adb is, in this order: the program named by MILKCRATEVINYL_ADB (the
    # Windows package sets it), the one in the tools of the build output
    # directory (BOD), the one on the PATH.
    named = os.environ.get("MILKCRATEVINYL_ADB")
    if named:
        if not os.path.exists(named):
            raise RuntimeError(
                f"MILKCRATEVINYL_ADB is set to {named}, which is missing"
            )
        return named
    bod = os.environ.get("MILKCRATEVINYL_BOD")
    if bod:
        program = os.path.join(
            bod, "android-dev", "android-sdk", "platform-tools",
            "adb.exe" if os.name == "nt" else "adb",
        )
        if os.path.exists(program):
            return program
    found = shutil.which("adb")
    if found:
        return found
    raise RuntimeError(
        "adb cannot be found: set MILKCRATEVINYL_ADB to the program, put adb "
        "on the PATH, or set MILKCRATEVINYL_BOD and run "
        "scripts/setup-android-tools"
    )


def adb(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        [adb_program(), "-d", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"adb {' '.join(args[:2])}: {r.stdout.strip()} {r.stderr.strip()}"
        )
    return r.stdout


def on_phone(dest: str) -> dict[str, int]:
    # relative path -> size of every file already under dest.
    out = adb(
        "shell",
        f"cd '{dest}' 2>/dev/null && find . -type f -exec stat -c '%s|%n'"
        " {} +",
        check=False,
    )
    have = {}
    for line in out.splitlines():
        size, _, name = line.partition("|")
        if size.isdigit() and name.startswith("./"):
            have[name[2:].lower()] = int(
                size
                # the card ignores capitals: a folder named differently only in
                # case
                # is the same folder
            )
    return have
