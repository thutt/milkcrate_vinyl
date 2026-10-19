# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Builds the Android icon from icon-master.png (a finished picture with a dark
# rounded square behind the crate), as the two layers of an adaptive icon:
#     render_icon_png.py SAMPLE_DIR   (the preview sheet goes to SAMPLE_DIR)
# The foreground layer is the crate with the picture's dark background taken
# out: a pixel that is the background colour becomes transparent, one that is
# the background slightly darkened (a shadow) becomes partly transparent black,
# and the rounded corners are cut away.  The
# layer is scaled so the crate stays inside the area that every icon shape
# shows (the middle 66 of the 108 dp), over a flat background layer.  Writes
# both layers and the launcher icons for old phones into ../android/res.
import os
import sys

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "android", "res")
BACKGROUND = QColor("#f8e8a0")  # buttery yellow
SCALE = 0.30  # the 1024 picture on the 432 pixel layer
INNER = 450  # half the side of the part of the picture that is used
CORNER = 170  # its corner radius
FADE = 50  # the edge fades out over this width
SAME = 8  # a pixel this close to the background colour is background

app = QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
master = QImage(os.path.join(HERE, "icon-master.png")).convertToFormat(
    QImage.Format.Format_ARGB32
)


def picture():
    ptr = master.constBits()
    ptr.setsize(master.sizeInBytes())
    bgra = np.frombuffer(ptr, np.uint8).reshape(1024, 1024, 4)
    return bgra[:, :, 2::-1].astype(np.float64)  # red, green, blue


def background_model(rgb):
    # the picture's background is a smooth dark gradient: fit a quadratic to
    # the pixels along its four sides (the black corners are left out)
    ys, xs = np.mgrid[0:1024, 0:1024]
    x, y = (xs - 512) / 512.0, (ys - 512) / 512.0
    terms = np.stack([x * 0 + 1, x, y, x * x, y * y, x * y], axis=-1)
    side = ((xs < 100) | (xs > 924) | (ys < 60) | (ys > 970)) & (
        rgb.sum(axis=2) > 60
    )
    side &= (np.abs(x) < 0.8) | (np.abs(y) < 0.8)
    fit = np.linalg.lstsq(terms[side], rgb[side], rcond=None)[0]
    return terms @ fit


def edge(x, y):
    # distance outside the rounded square (negative inside)
    qx = np.abs(x - 512) - (INNER - CORNER)
    qy = np.abs(y - 512) - (INNER - CORNER)
    outside = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
    return outside + np.minimum(np.maximum(qx, qy), 0) - CORNER


def shift(mask, dy, dx):
    return np.roll(np.roll(mask, dy, axis=0), dx, axis=1)


def grow(mask, n):
    for _ in range(n):
        mask = (
            mask
            | shift(mask, 1, 0)
            | shift(mask, -1, 0)
            | shift(mask, 0, 1)
            | shift(mask, 0, -1)
        )
    return mask


def inside(solid):
    # The crate and records, filled in: whatever the outside cannot reach
    # without crossing a solid pixel (the black record has lit grooves that
    # would otherwise be see-through).
    wall = grow(solid, 2)
    reached = np.zeros_like(wall)
    reached[0, :] = ~wall[0, :]
    reached[-1, :] = ~wall[-1, :]
    reached[:, 0] = ~wall[:, 0]
    reached[:, -1] = ~wall[:, -1]
    while True:
        more = grow(reached, 1) & ~wall
        if (more == reached).all():
            break
        reached = more
    return ~grow(reached, 3)


def foreground():
    rgb = picture()
    bg = background_model(rgb)
    k = np.clip((rgb * bg).sum(2) / (bg * bg).sum(2), 0, 1)
    residual = np.sqrt(((rgb - k[..., None] * bg) ** 2).sum(2))
    ramp = np.clip((residual - SAME) / SAME, 0, 1)
    # light darkening (a shadow) stays see-through; the record, which is the
    # background colour darkened a lot, is solid; grain in the background is
    # ignored
    shade = np.clip((1 - k - 0.10) / 0.55, 0, 1)
    alpha = np.maximum(ramp, shade)
    alpha = np.maximum(alpha, inside(alpha > 0.9))
    # the colour that gives this pixel over the background colour
    safe = np.maximum(alpha, 1e-3)[..., None]
    color = np.clip((rgb - (1 - alpha[..., None]) * bg) / safe, 0, 255)
    ys, xs = np.mgrid[0:1024, 0:1024]
    cut = np.clip(1 - edge(xs + 0.5, ys + 0.5) / FADE, 0, 1)
    alpha = alpha * cut
    out = np.empty((1024, 1024, 4), np.uint8)
    out[..., 0] = color[..., 2]
    out[..., 1] = color[..., 1]
    out[..., 2] = color[..., 0]
    out[..., 3] = np.round(alpha * 255)
    big = QImage(
        out.tobytes(), 1024, 1024, 4096, QImage.Format.Format_ARGB32
    ).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    size = round(1024 * SCALE)
    small = big.scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    img = QImage(432, 432, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.drawImage(round(216 - size / 2), round(216 - size / 2), small)
    p.end()
    return img.convertToFormat(QImage.Format.Format_ARGB32)


fg = foreground()
bg = QImage(432, 432, QImage.Format.Format_ARGB32)
bg.fill(BACKGROUND)
nodpi = os.path.join(RES, "drawable-nodpi")
fg.save(os.path.join(nodpi, "ic_launcher_foreground.png"))
bg.save(os.path.join(nodpi, "ic_launcher_background.png"))


def composed():
    img = QImage(432, 432, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.drawImage(0, 0, bg)
    p.drawImage(0, 0, fg)
    p.end()
    return img


full = composed()


# launcher icons for phones before Android 8: the middle 72 of the 108 dp
def legacy(shape, px):
    out = QImage(px, px, QImage.Format.Format_ARGB32)
    out.fill(0)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    r = QRectF(0, 0, px, px)
    if shape == "circle":
        path.addEllipse(r)
    elif shape == "squircle":
        path.addRoundedRect(r, px * 0.3, px * 0.3)
    else:
        path.addRoundedRect(r, px * 0.12, px * 0.12)
    p.setClipPath(path)
    p.drawImage(QRectF(0, 0, px, px), full.copy(54, 54, 324, 324))
    p.end()
    return out


mip = os.path.join(RES, "mipmap-xxxhdpi")
legacy("squircle", 192).save(os.path.join(mip, "ic_launcher.png"))
legacy("circle", 192).save(os.path.join(mip, "ic_launcher_round.png"))
sheet = QImage(4 * 260 + 50, 260 + 120, QImage.Format.Format_ARGB32)
sheet.fill(QColor("#e8e8e8"))
p = QPainter(sheet)
p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.drawImage(QRectF(10, 10, 260, 260), full)
for i, s in enumerate(["circle", "squircle", "square"]):
    p.drawImage(QRectF(280 + i * 260, 10, 250, 250), legacy(s, 432))
for i, s in enumerate(["circle", "squircle"]):
    p.drawImage(QRectF(20 + i * 70, 280, 48, 48), legacy(s, 192))
    p.drawImage(QRectF(170 + i * 40, 288, 32, 32), legacy(s, 192))
p.setPen(QColor("black"))
p.drawText(
    10, 365, "full layers | circle | squircle | rounded square | small sizes"
)
p.end()
sheet.save(os.path.join(sys.argv[1], "sample.png"))
