"""Live ECG drawn like a bedside monitor on ECG paper.

BLE delivers ECG in bursts (one packet of ~70 samples every ~0.5 s). To make
the trace move smoothly, samples go into a jitter queue and are released at
exactly 130 Hz from the frame clock. A write head sweeps left to right over a
5 s ring buffer: the new trace is drawn up to the head, the previous sweep
stays visible (faded) beyond a short erase gap ahead of it.

Recording does not depend on any of this: the session stores ECG samples with
their arrival timestamps before they reach the queue.
"""

from __future__ import annotations

from collections import deque
import math

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..ble import ECG_HZ
from . import theme
from .widgets import fast_polyline, mix

SWEEP_SAMPLES = 650  # 5 s at 130 Hz
START_SAMPLES = 90  # ~0.7 s buffered before playout starts
BEHIND_SAMPLES = 260  # 2 s behind: catch up
CATCH_UP_TO = 130  # ...down to 1 s of queue
ERASE_GAP = 18  # samples cleared ahead of the head
MAX_FRAME_DT = 0.1  # a stalled frame never releases more than 0.1 s
PAPER_MM = 125  # 5 s at 25 mm/s
MM_PER_MV = 10
TRACE_W = 1.6  # px


class Sweep:
    """Jitter queue and ring buffer. Pure state, advanced once per frame."""

    def __init__(self):
        self.buf = [0.0] * SWEEP_SAMPLES
        self.head = 0  # next write position
        self.filled = 0  # samples written so far, up to SWEEP_SAMPLES
        self.queue: deque[int] = deque()
        self.playing = False
        self.carry = 0.0  # fractional samples owed to the next frame
        self.last = 0.0

    def push(self, samples) -> None:
        self.queue.extend(samples)

    def advance(self, t: float) -> None:
        """Release samples for the time elapsed since the previous frame."""
        dt = min(MAX_FRAME_DT, t - (self.last or t))
        self.last = t
        if not self.playing:
            if len(self.queue) < START_SAMPLES:
                return
            self.playing = True
        self.carry += dt * ECG_HZ
        n = int(self.carry)
        self.carry -= n
        if len(self.queue) > BEHIND_SAMPLES:
            n += len(self.queue) - CATCH_UP_TO
        for _ in range(min(n, len(self.queue))):
            self.buf[self.head] = self.queue.popleft()
            self.head = (self.head + 1) % SWEEP_SAMPLES
            if self.filled < SWEEP_SAMPLES:
                self.filled += 1
        if not self.queue:
            self.playing = False


class EcgSweep(QWidget):
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("Live ECG sweep, 5 seconds")
        self.sweep = Sweep()
        self.connected = False
        self._paper: QPixmap | None = None
        self._paper_key = None

    def sizeHint(self) -> QSize:
        return QSize(600, 120)

    def restyle(self) -> None:
        self._paper = None  # rebuild the grid in the new theme colours
        self.update()

    def _paper_pixmap(self) -> QPixmap:
        """ECG paper: 1 mm minor and 5 mm major lines, cached per size/theme."""
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, theme.is_dark())
        if self._paper is not None and self._paper_key == key:
            return self._paper
        W, H = self.width(), self.height()
        pm = QPixmap(max(1, round(W * dpr)), max(1, round(H * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(theme.color("surface"))
        p = QPainter(pm)
        mm, mid = W / PAPER_MM, H * 0.6
        minor = QPen(theme.color("paper"), 1)
        major = QPen(theme.color("paper-major"), 1)
        i = 0
        while i * mm <= W:
            p.setPen(major if i % 5 == 0 else minor)
            p.drawLine(QPointF(i * mm, 0), QPointF(i * mm, H))
            i += 1
        i = -math.ceil(mid / mm)
        while mid + i * mm <= H:
            p.setPen(major if i % 5 == 0 else minor)
            p.drawLine(QPointF(0, mid + i * mm), QPointF(W, mid + i * mm))
            i += 1
        p.end()
        self._paper, self._paper_key = pm, key
        return pm

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._paper_pixmap())
        sw = self.sweep
        if not sw.filled:
            p.setPen(theme.color("ink-3"))
            p.setFont(theme.ui_font(12.5))
            msg = (
                "Waiting for ECG…"
                if self.connected
                else "Connect a Polar H10 to see the live ECG"
            )
            p.drawText(self.rect(), Qt.AlignCenter, msg)
            return

        p.setRenderHint(QPainter.Antialiasing)
        N, buf = SWEEP_SAMPLES, sw.buf
        mm, mid = W / PAPER_MM, H * 0.6
        kx, ky = W / (N - 1), mm * MM_PER_MV / 1000  # px per sample, px per uV

        def poly(a: int, b: int) -> QPolygonF:
            return QPolygonF([QPointF(k * kx, mid - buf[k] * ky) for k in range(a, b)])

        trace = theme.color("trace")
        if sw.head >= 2:
            fast_polyline(p, poly(0, sw.head), trace, TRACE_W)  # current sweep
        if sw.filled >= N and N - (sw.head + ERASE_GAP) >= 2:
            # Previous sweep, erased just ahead of the head. A pre-blended
            # colour stands in for 50 % opacity (overlapping passes would
            # otherwise darken it).
            faded = mix(trace, theme.color("surface"), 0.5)
            fast_polyline(p, poly(sw.head + ERASE_GAP, N), faded, TRACE_W)

        # Write head: a dot with a soft glow.
        k = (sw.head - 1) % N
        centre = QPointF(k * kx, mid - buf[k] * ky)
        p.setPen(Qt.NoPen)
        for r, a in ((8, 0.08), (6, 0.12), (4.2, 0.2)):
            glow = QColor(trace)
            glow.setAlphaF(a)
            p.setBrush(glow)
            p.drawEllipse(centre, r, r)
        p.setBrush(trace)
        p.drawEllipse(centre, 2.6, 2.6)
