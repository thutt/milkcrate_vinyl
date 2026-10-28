# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The progress bar of the Samples tab: a seek bar with the interface of a
# QSlider (setRange, setValue, value, isSliderDown, sliderMoved,
# sliderReleased) and two markers that bound a region of the track.  The
# start marker is an arrow pointing right, the end marker an arrow pointing
# left.  The start is never after the end, and the position handle stays
# inside the region.  The time of the start floats above its arrow and the
# time of the end below its arrow (so that two markers that are close
# together never cover each other's time).  While a marker is held, the
# length of the region is shown in the middle of it.  The marker that was
# grabbed last is outlined; with the keyboard focus on the bar, the left and
# right arrow keys move that marker by a tenth of a second.
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PyQt6.QtWidgets import QSizePolicy, QWidget

MARGIN = 14  # room at each end of the bar for the markers
LANE = 16  # the strips above and below the bar that hold the times
HEIGHT = 2 * LANE + 28
ARROW = 12  # width of the marker arrows
GRAB = 4  # pixels beyond the marker line that still take hold of it
KEY_STEP_MS = 100  # what an arrow key moves the chosen marker by


def clock(ms: int) -> str:
    # minutes:seconds.tenths
    tenths = ms // 100
    return f"{tenths // 600}:{tenths // 10 % 60:02d}.{tenths % 10}"


class RangeSeek(QWidget):
    sliderMoved = pyqtSignal(int)
    sliderReleased = pyqtSignal()
    regionChanged = pyqtSignal(int, int)  # start and end, in milliseconds
    regionReleased = pyqtSignal()  # a marker was let go

    def __init__(self, parent=None):
        super().__init__(parent)
        self._max = 0
        self._value = 0
        self._start = 0
        self._end = 0
        self._end_follows = True  # the end is the end of the track until moved
        self._drag: str | None = None
        self._chosen = "start"  # the marker the arrow keys move
        self._offset = 0.0  # where on the arrow it was taken hold of
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setToolTip(
            "Drag the left arrow to set where the snippet starts and the"
            " right arrow to set where it ends."
        )

    def sizeHint(self) -> QSize:
        return QSize(300, HEIGHT)

    # -- the interface of a slider ------------------------------------------

    def setRange(self, _low: int, high: int) -> None:
        self._max = max(high, 0)
        if self._end_follows or self._end > self._max:
            self._end = self._max
        self._start = min(self._start, self._end)
        self.regionChanged.emit(self._start, self._end)
        self.update()

    def setValue(self, value: int) -> None:
        if self._drag != "pos":
            self._value = value
            self.update()

    def value(self) -> int:
        return self._value

    def maximum(self) -> int:
        return self._max

    def isSliderDown(self) -> bool:
        return self._drag == "pos"

    # -- the region ----------------------------------------------------------

    def region(self) -> tuple[int, int]:
        return self._start, self._end

    def reset_region(self) -> None:
        # The whole track again (a new track was chosen).
        self._start = 0
        self._end = self._max
        self._end_follows = True
        self.regionChanged.emit(self._start, self._end)
        self.update()

    # -- drawing and the mouse ----------------------------------------------

    def _x(self, ms: int) -> float:
        span = max(self.width() - 2 * MARGIN, 1)
        return MARGIN + (span * ms / self._max if self._max else 0)

    def _ms(self, x: float) -> int:
        span = max(self.width() - 2 * MARGIN, 1)
        return int(min(max((x - MARGIN) / span, 0.0), 1.0) * self._max)

    def _hit(self, x: float) -> str:
        # Which part of the bar a press at x takes hold of.
        start, end = self._x(self._start), self._x(self._end)
        in_start = start - GRAB <= x <= start + ARROW
        in_end = end - ARROW <= x <= end + GRAB
        if in_start and in_end:
            return "start" if x < (start + end) / 2 else "end"
        if in_start:
            return "start"
        if in_end:
            return "end"
        return "pos"

    def _move(self, x: float) -> None:
        ms = self._ms(x - self._offset)
        if self._drag == "start":
            self._start = min(ms, self._end)
            self.regionChanged.emit(self._start, self._end)
        elif self._drag == "end":
            self._end = max(ms, self._start)
            self._end_follows = False
            self.regionChanged.emit(self._start, self._end)
        elif self._drag == "pos":
            self._value = min(max(ms, self._start), self._end)
            self.sliderMoved.emit(self._value)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._max:
            return
        x = event.position().x()
        self._drag = self._hit(x)
        self._offset = 0.0
        if self._drag in ("start", "end"):
            self._chosen = self._drag
            # (taking hold of an arrow does not move it)
            self._offset = x - self._x(
                self._start if self._drag == "start" else self._end
            )
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._move(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._drag:
            self._move(event.position().x())

    def mouseReleaseEvent(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag == "pos":
            self.sliderReleased.emit()
        elif drag:
            self.regionReleased.emit()
        self.update()

    def _label(self, painter, text: str, x: float, above: bool) -> None:
        palette = self.palette()
        width = painter.fontMetrics().horizontalAdvance(text) + 10
        left = min(max(x - width / 2, 0), self.width() - width)
        top = 0 if above else self.height() - LANE + 1
        box = QRectF(left, top, width, LANE - 1)
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
        painter.setBrush(palette.color(QPalette.ColorRole.ToolTipBase))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(palette.color(QPalette.ColorRole.ToolTipText))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def keyPressEvent(self, event) -> None:
        step = {Qt.Key.Key_Left: -KEY_STEP_MS, Qt.Key.Key_Right: KEY_STEP_MS}
        if (
            event.key() not in step
            or event.modifiers() != Qt.KeyboardModifier.NoModifier
            or not self._max
        ):
            super().keyPressEvent(event)
            return
        move = step[event.key()]
        if self._chosen == "start":
            self._start = min(max(self._start + move, 0), self._end)
        else:
            self._end = min(max(self._end + move, self._start), self._max)
            self._end_follows = False
        self.regionChanged.emit(self._start, self._end)
        self.regionReleased.emit()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        highlight = palette.color(QPalette.ColorRole.Highlight)
        centre = self.height() / 2
        start, end = self._x(self._start), self._x(self._end)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.color(QPalette.ColorRole.Mid))
        painter.drawRoundedRect(
            QRectF(MARGIN, centre - 3, self.width() - 2 * MARGIN, 6), 3, 3
        )
        band = highlight.lighter(130)
        band.setAlpha(120)
        painter.setBrush(band)
        painter.drawRect(QRectF(start, centre - 8, end - start, 16))
        painter.setBrush(palette.color(QPalette.ColorRole.WindowText))
        painter.drawRect(QRectF(self._x(self._value) - 1, centre - 10, 2, 20))
        painter.setBrush(highlight)
        painter.setPen(QPen(highlight, 2))
        for x in (start, end):
            painter.drawLine(QPointF(x, centre - 12), QPointF(x, centre + 12))
        outline = QPen(palette.color(QPalette.ColorRole.WindowText), 1.5)
        for name, x, tip in (
            ("start", start, start + ARROW),
            ("end", end, end - ARROW),
        ):
            chosen = name == self._chosen
            painter.setPen(outline if chosen else QPen(highlight, 2))
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(x, centre - 11),
                        QPointF(tip, centre),
                        QPointF(x, centre + 11),
                    ]
                )
            )
        if self._max:
            self._label(painter, clock(self._start), start, True)
            self._label(painter, clock(self._end), end, False)
        if self._drag in ("start", "end"):
            length = clock(self._end - self._start)
            room = painter.fontMetrics().horizontalAdvance(length) + 16
            if end - start > room:
                painter.setPen(palette.color(QPalette.ColorRole.WindowText))
                painter.drawText(
                    QRectF(start, centre - 8, end - start, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    length,
                )
