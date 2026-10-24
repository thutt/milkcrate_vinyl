# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Measures the loudness of one audio file (EBU R128) with ffmpeg.  Shared by
# loudness.py (the whole library) and file_analysis.py (selected files); a
# module with a main cannot be imported by another, so it is kept here.
#
# measure(path) -> (integrated loudness in LUFS, true peak in dBFS), or None
# when the file could not be measured.
from __future__ import annotations

import re
import subprocess


def measure(path: str):
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostats",
            "-hide_banner",
            "-i",
            path,
            "-vn",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    summary = proc.stderr.rsplit("Summary:", 1)[-1]
    integrated = re.search(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)
    if (
        proc.returncode
        or not integrated
        or not peak
        or "inf" in integrated.group(1)
    ):
        return None
    return float(integrated.group(1)), float(
        "-120" if "inf" in peak.group(1) else peak.group(1)
    )
