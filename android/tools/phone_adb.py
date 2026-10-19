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
import subprocess


def adb_program() -> str:
    # adb comes from the tools in the build output directory (BOD).
    bod = os.environ.get("MILKCRATEVINYL_BOD")
    if not bod:
        raise RuntimeError(
            "MILKCRATEVINYL_BOD is not set, so adb cannot be found: "
            "source scripts/setup --venv DIR --bod DIR --database FILE"
        )
    program = os.path.join(
        bod, "android-dev", "android-sdk", "platform-tools", "adb"
    )
    if not os.path.exists(program):
        raise RuntimeError(
            f"adb is not installed: {program} is missing; "
            "run scripts/setup-android-tools"
        )
    return program


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
