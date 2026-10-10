"""Heart rate and HRV trend with one coloured lane per label.

Layout, top to bottom: HR panel (5-beat moving average, opening-silence
reference line, signal events), RMSSD panel for the selected window, label
lanes, time axis. Redrawn every frame by the window's loop; all data comes from
the session through binary-searched slices, so cost scales with what is
visible, not with session length.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import hrv
from ..config import LABELS
from . import theme
from .widgets import clock_text, fast_polyline

PAD_L, PAD_R, PAD_TOP = 40, 40, 10
LANE_H, AXIS_H = 12, 16
TICK_STEPS = (1, 2, 5, 10, 20, 25, 50)


def ticks(lo: float, hi: float, n: int) -> list[float]:
    """Round tick values covering lo..hi with at most about n steps."""
    step = next((s for s in TICK_STEPS if (hi - lo) / s <= n), 100)
    v = math.ceil(lo / step) * step
    out = []
    while v <= hi:
        out.append(v)
        v += step
    return out


def pixel_line(points, x, y) -> QPolygonF:
    """Map (t, value) points to pixels, averaging points that share a pixel column.

    A long session can hold several beats per pixel; stroking all of them is
    slow and looks no different, so the line keeps at most one vertex per px.
    """
    out, col, sx, sy, n = [], None, 0.0, 0.0, 0
    for t, v in points:
        px = x(t)
        c = int(px)
        if c != col and n:
            out.append(QPointF(sx / n, sy / n))
            sx = sy = n = 0
        col = c
        sx += px
        sy += y(v)
        n += 1
    if n:
        out.append(QPointF(sx / n, sy / n))
    return QPolygonF(out)


def label_color(label: str):
    return theme.color(LABELS[label]["color"] if label in LABELS else "line")


class TrendChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(200)
        self.setAccessibleName("Heart rate, RMSSD and label timeline")
        self.session = None
        self.now = 0.0
        self.connected = False
        self.view_s = 300  # 0 = whole recording
        self.hrv_win = 15
        self.ref = None  # cached opening-silence reference (2 Hz)
        self.f_mono = theme.mono_font(10.5)
        self.f_small = theme.ui_font(10, QFont.DemiBold)
        self.f_event = theme.ui_font(10.5, QFont.DemiBold)
        self.f_empty = theme.ui_font(12.5)
        self.f_lane = theme.ui_font(11)
        self._drawn = (None, 0.0)  # (content key, time) of the last paint

    # -- redraw gating ------------------------------------------------------

    def _key(self):
        """Everything except the passing of time that changes the picture."""
        s = self.session
        return (
            len(s.beats), s.beats[-1].t if s.beats else 0, len(s.hrv[self.hrv_win]),
            len(s.events), s.phase, self.view_s, self.hrv_win, self.connected,
            bool(self.ref and self.ref["ok"]), self.width(), self.height(),
            theme.is_dark(),
        )  # fmt: skip

    def needs_redraw(self, now: float) -> bool:
        """True when new data arrived or time moved the chart by half a pixel.

        At a 5 min view one pixel is ~0.35 s, so the chart repaints a few times
        a second instead of every frame, leaving the frame budget to the ECG.
        """
        s = self.session
        key, drawn_at = self._drawn
        if s is None or key != self._key():
            return True
        if s.phase == "done":
            return False  # frozen
        span = self.view_s or (max(120, now - s.t0) if s.phase != "idle" else 300)
        sec_per_px = span / max(1, self.width() - PAD_L - PAD_R)
        return now - drawn_at >= sec_per_px / 2

    # -- drawing helpers ----------------------------------------------------

    @staticmethod
    def _text(p, x, y, s, align="left"):
        """Draw text vertically centred on y, anchored left, right or centre."""
        w = 400
        if align == "right":
            r, flags = QRectF(x - w, y - 10, w, 20), Qt.AlignRight
        elif align == "center":
            r, flags = QRectF(x - w / 2, y - 10, w, 20), Qt.AlignHCenter
        else:
            r, flags = QRectF(x, y - 10, w, 20), Qt.AlignLeft
        p.drawText(r, flags | Qt.AlignVCenter, s)

    def _hline(self, p, x1, x2, y):
        p.drawLine(QPointF(x1, y), QPointF(x2, y))

    # -- paint --------------------------------------------------------------

    def paintEvent(self, _):
        s = self.session
        W, H = self.width(), self.height()
        if s is None or W < 50 or H < 80:
            return
        self._drawn = (self._key(), self.now)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = theme.color

        if not s.beats:
            p.setPen(c("ink-3"))
            p.setFont(self.f_empty)
            msg = (
                "Waiting for beats from the Polar H10…"
                if self.connected
                else "Connect a Polar H10 to see live heart rate and HRV"
            )
            p.drawText(self.rect(), Qt.AlignCenter, msg)
            return

        active = s.phase != "idle"
        t_now = s.end_time(self.now)
        span = self.view_s or (max(120, t_now - s.t0) if active else 300)
        tA = t_now - span
        L, R, top = PAD_L, W - PAD_R, PAD_TOP

        ivs = [iv for iv in s.intervals(self.now) if iv["t2"] >= tA] if active else []
        lanes = list(dict.fromkeys(iv["label"] for iv in ivs))
        lanes_h = max(LANE_H, len(lanes) * (LANE_H + 2))
        lanes_top = H - AXIS_H - lanes_h
        hv_h = max(40, (lanes_top - top - 24) * 0.3)
        hv_top = lanes_top - 10 - hv_h
        hr_bot = hv_top - 14

        def x(t):
            return L + (t - tA) / span * (R - L)

        p.setFont(self.f_mono)
        thin = QPen(c("line"), 1)

        # ---- HR panel -----------------------------------------------------
        pts = [
            (b.t, b.hr_smooth)
            for b in hrv.beats_in(s.beats, tA - 5, t_now)
            if b.hr_smooth is not None
        ]
        lo, hi = 55.0, 90.0
        if pts:
            vals = [v for _, v in pts]
            lo, hi = min(vals) - 4, max(vals) + 4
            if hi - lo < 16:
                mid = (hi + lo) / 2
                lo, hi = mid - 8, mid + 8

        def y(v):
            return top + (1 - (v - lo) / (hi - lo)) * (hr_bot - top)

        for v in ticks(lo, hi, 5):
            p.setPen(thin)
            self._hline(p, L, R, y(v))
            p.setPen(c("ink-3"))
            self._text(p, L - 6, y(v), f"{v:g}", "right")
        self._text(p, R + 5, top + 2, "bpm")

        ref = self.ref if active else None
        if ref and ref["ok"] and lo < ref["hr"] < hi:
            pen = QPen(c("ink-3"), 1)
            pen.setDashPattern([3, 4])
            p.setPen(pen)
            self._hline(p, L, R, y(ref["hr"]))
            p.setPen(c("ink-3"))
            self._text(p, R + 5, y(ref["hr"]), "ref")

        p.save()
        p.setClipRect(QRectF(L, 0, R - L, H))
        if len(pts) > 1:
            line = pixel_line(pts, x, y)
            area = QPainterPath()
            area.addPolygon(line)
            area.lineTo(line.last().x(), hr_bot)
            area.lineTo(line.first().x(), hr_bot)
            area.closeSubpath()
            fill = c("trace")
            fill.setAlphaF(0.07)
            p.setRenderHint(
                QPainter.Antialiasing, False
            )  # 7 % fill: AA invisible, 5x slower
            p.fillPath(area, fill)
            p.setRenderHint(QPainter.Antialiasing, True)
            fast_polyline(p, line, c("trace"), 1.7)
            p.setPen(Qt.NoPen)
            p.setBrush(c("trace"))
            p.drawEllipse(line.last(), 3.2, 3.2)

        # Signal events (sensor lost / reconnected) as red markers.
        p.setFont(self.f_event)
        for e in s.events:
            if e["kind"] == "signal" and tA <= e["t"] <= t_now:
                ex = x(e["t"])
                p.setPen(QPen(c("crit"), 1))
                p.drawLine(QPointF(ex, top), QPointF(ex, lanes_top + lanes_h))
                self._text(p, ex + 5, top + 3, e["text"])
        p.restore()

        # ---- RMSSD panel --------------------------------------------------
        p.setFont(self.f_mono)
        hv = [
            (t, v)
            for t, v in hrv.series_in(s.hrv[self.hrv_win], tA - 2, t_now)
            if v is not None
        ]
        hmax = max(30, math.ceil(max(v for _, v in hv) / 10) * 10 + 5) if hv else 60

        def yh(v):
            return hv_top + (1 - v / hmax) * hv_h

        for v in (0, round(hmax / 10) * 5, math.floor(hmax / 5) * 5):
            p.setPen(thin)
            self._hline(p, L, R, yh(v))
            p.setPen(c("ink-3"))
            self._text(p, L - 6, yh(v), str(v), "right")
        self._text(p, R + 5, hv_top + 2, "ms")
        p.setPen(c("accent"))
        p.setFont(self.f_small)
        self._text(p, L + 4, hv_top + 6, f"RMSSD {self.hrv_win} s")
        if len(hv) > 1:
            p.save()
            p.setClipRect(QRectF(L, 0, R - L, H))
            fast_polyline(p, pixel_line(hv, x, yh), c("accent"), 1.4)
            p.restore()

        # ---- Label lanes --------------------------------------------------
        if not lanes:
            p.fillRect(QRectF(L, lanes_top, R - L, LANE_H), c("surface-2"))
            p.setPen(c("ink-3"))
            p.setFont(self.f_lane)
            self._text(p, (L + R) / 2, lanes_top + LANE_H / 2 + 1,
                       "Label lanes appear once recording starts", "center")  # fmt: skip
        p.save()
        p.setClipRect(QRectF(L, lanes_top, R - L, lanes_h))
        p.setFont(self.f_small)
        for i, label in enumerate(lanes):
            ly = lanes_top + i * (LANE_H + 2)
            p.fillRect(QRectF(L, ly, R - L, LANE_H), c("surface-2"))
            col = label_color(label)
            for iv in ivs:
                if iv["label"] != label:
                    continue
                a, z = max(x(iv["t1"]), L), min(x(iv["t2"]), R)
                p.fillRect(QRectF(a, ly, max(1.5, z - a), LANE_H), col)
                if z - a > 64:
                    p.setPen(c("on-label"))
                    self._text(p, a + 5, ly + LANE_H / 2 + 0.5, label)
        p.restore()

        # ---- Time axis ----------------------------------------------------
        p.setPen(c("ink-3"))
        p.setFont(self.f_mono)
        t0 = s.t0 if active else t_now
        step = 60 if span <= 360 else 180 if span <= 1000 else 600
        t = math.ceil((tA - t0) / step) * step
        while t <= t_now - t0 + 0.01:
            px = x(t0 + t)
            if L + 12 <= px <= R - 12:
                if active and t >= 0:
                    label = clock_text(t)  # time into the recording
                elif active:
                    label = "-" + clock_text(-t)  # live view before the start
                else:
                    label = "now" if abs(t) < 1 else "-" + clock_text(-t)
                self._text(p, px, H - AXIS_H / 2, label, "center")
            t += step
