# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Renders the app icon art (SVG) into the PNG layers of the Android icon:
#     render_icon.py SAMPLE_DIR   (the preview sheet goes to SAMPLE_DIR)
import os
import sys
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QColor, QPainterPath
from PyQt6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "android", "res")
app = QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])


def layer(src, size):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    QSvgRenderer(src).render(p)
    p.end()
    return img


fg = layer(os.path.join(HERE, "milkcrate-foreground.svg"), 432)
bg = layer(os.path.join(HERE, "milkcrate-background.svg"), 432)
nodpi = os.path.join(RES, "drawable-nodpi")
fg.save(os.path.join(nodpi, "ic_launcher_foreground.png"))
bg.save(os.path.join(nodpi, "ic_launcher_background.png"))


def composed(size):
    img = QImage(432, 432, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.drawImage(0, 0, bg)
    p.drawImage(0, 0, fg)
    p.end()
    return img


full = composed(432)


# legacy launcher icons: rounded square / circle, the visible 72/108 of the
# layers scaled up
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
    src = full.copy(54, 54, 324, 324)  # central 72dp of the 108dp canvas
    p.drawImage(QRectF(0, 0, px, px), src)
    p.end()
    return out


mip = os.path.join(RES, "mipmap-xxxhdpi")
legacy("squircle", 192).save(os.path.join(mip, "ic_launcher.png"))
legacy("circle", 192).save(os.path.join(mip, "ic_launcher_round.png"))
# sample sheet: raw layers, then circle / squircle / rounded square, then
# home-screen size
sheet = QImage(4 * 260 + 50, 260 + 120, QImage.Format.Format_ARGB32)
sheet.fill(QColor("#2a2a2e"))
p = QPainter(sheet)
p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.drawImage(QRectF(10, 10, 260, 260), full)
for i, s in enumerate(["circle", "squircle", "square"]):
    p.drawImage(QRectF(280 + i * 260, 10, 250, 250), legacy(s, 432))
for i, s in enumerate(["circle", "squircle"]):
    p.drawImage(QRectF(20 + i * 70, 280, 48, 48), legacy(s, 192))
    p.drawImage(QRectF(170 + i * 40, 288, 32, 32), legacy(s, 192))
p.setPen(QColor("white"))
p.drawText(
    10, 365, "full layers | circle | squircle | rounded square | small sizes"
)
p.end()
sheet.save(os.path.join(sys.argv[1], "sample.png"))
