# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Estimates the tempo (BPM) of an audio file from its sound.
#
# Decodes about a minute from the middle of the track with ffmpeg, measures how
# the energy changes from moment to
# moment (the onset strength), and finds the beat period with an autocorrelation
# weighted toward common tempos.
# Returns the BPM, a confidence (how much the best period stands out) and the
# half / double alternatives.
from __future__ import annotations

import subprocess

import mutagen
import numpy as np

RATE = 22050
HOP = 128
FFT = 1024
MIN_BPM, MAX_BPM = 60.0, 200.0
SECONDS = 60
# seconds analysed per window; three windows (at 25 %, 50 % and 75 % of the
# track)
WINDOW = 30
AGREE = 0.03  # windows agree when within 3 % of the median


def decode(
    path: str,
    seconds: int = SECONDS,
    centre: float = 0.5,
    length: float | None = None,
) -> np.ndarray:
    length = length or mutagen.File(path).info.length
    start = max(0.0, min(length * centre - seconds / 2, length - seconds))
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start:.2f}",
            "-t",
            str(seconds),
            "-i",
            path,
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32)


def onset_strength(samples: np.ndarray) -> np.ndarray:
    window = np.hanning(FFT)
    frames = np.lib.stride_tricks.sliding_window_view(samples, FFT)[::HOP]
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1))
    spectrum = np.log1p(
        100 * spectrum[:, : FFT // 4]
    )  # up to ~5.5 kHz, compressed
    flux = np.maximum(spectrum[1:] - spectrum[:-1], 0).sum(axis=1)
    flux -= np.convolve(
        flux, np.ones(86) / 86, mode="same"
    )  # remove the slow trend
    return np.maximum(flux, 0)


def estimate(
    path: str,
    seconds: int = SECONDS,
    centre: float = 0.5,
    length: float | None = None,
) -> dict:
    env = onset_strength(decode(path, seconds, centre, length))
    if len(env) < 400 or not env.any():
        return {
            "bpm": 0,
            "exact": 0.0,
            "confidence": 0.0,
            "half": 0,
            "double": 0,
        }
    env = (env - env.mean()) / (env.std() or 1)
    n = len(env)
    acf = np.fft.irfft(np.abs(np.fft.rfft(env, 2 * n)) ** 2)[:n]
    acf /= acf[0]
    fps = RATE / HOP
    bpms = np.arange(MIN_BPM, MAX_BPM + 0.25, 0.25)
    lags = fps * 60.0 / bpms
    base = np.interp(lags, np.arange(n), acf)
    score = (
        base
        + 0.5 * np.interp(2 * lags, np.arange(n), acf, right=0)
        + 0.25 * np.interp(4 * lags, np.arange(n), acf, right=0)
    )
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.8) ** 2)
    weighted = score * prior
    best = int(np.argmax(weighted))
    confidence = float(
        (weighted[best] - np.median(weighted)) / (weighted[best] + 1e-9)
    )
    bpm = float(bpms[best])
    return {
        "bpm": round(bpm),
        "exact": round(bpm, 1),
        "confidence": round(confidence, 2),
        "half": round(bpm / 2),
        "double": round(bpm * 2),
    }


def analyse(path: str) -> dict:
    # Tempo of a file from three windows, with how far they agree.  reliability:
    # "high" (all windows agree and the
    # beat stands out), "medium" (two agree), "low" (they disagree, the beat is
    # weak or the file is too short).
    length = mutagen.File(path).info.length
    centres = (0.5,) if length < 2 * WINDOW else (0.25, 0.5, 0.75)
    found = [estimate(path, WINDOW, c, length) for c in centres]
    tempos = np.array([f["exact"] for f in found])
    if not tempos.any():
        return {
            "bpm": 0,
            "reliability": "low",
            "agree": 0,
            "windows": len(found),
            "prominence": 0.0,
            "tempos": [],
        }
    median = float(np.median(tempos))
    close = [
        f
        for f in found
        if f["exact"] and abs(f["exact"] - median) <= AGREE * median
    ]
    bpm = int(round(float(np.median([f["exact"] for f in close]))))
    prominence = float(np.mean([f["confidence"] for f in close]))
    if len(found) < 3:
        level = "low"  # one short window: nothing to check it against
    elif len(close) == 3 and prominence >= 0.9:
        level = "high"
    elif len(close) >= 2 and prominence >= 0.7:
        level = "medium"
    else:
        level = "low"
    return {
        "bpm": bpm,
        "reliability": level,
        "agree": len(close),
        "windows": len(found),
        "prominence": round(prominence, 2),
        "tempos": [round(float(t), 1) for t in tempos],
    }
