"""Vitals strip: HR, RMSSD, % change against the opening silence, signal, beats.

Refreshed at 2 Hz by the window. Only the heart dot animates per frame.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import hrv
from . import theme
from .widgets import ElidedLabel, cap_label, set_tone

PULSE_S = 0.09  # heart dot stays enlarged this long after a good beat
PULSE_RAMP_S = 0.08  # CSS-like transition time in and out


class Heart(QWidget):
    """7 px red dot that swells to 1.8x on each good beat."""

    def __init__(self, reduce_motion: bool):
        super().__init__()
        self.setFixedSize(14, 14)
        self.scale = 1.0
        self.reduce_motion = reduce_motion

    def tick(self, now: float, last_beat: float) -> None:
        if self.reduce_motion:
            return
        age = now - last_beat
        if age < 0:
            s = 1.0
        elif age < PULSE_RAMP_S:
            s = 1 + 0.8 * age / PULSE_RAMP_S
        elif age < PULSE_S:
            s = 1.8
        elif age < PULSE_S + PULSE_RAMP_S:
            s = 1.8 - 0.8 * (age - PULSE_S) / PULSE_RAMP_S
        else:
            s = 1.0
        if s != self.scale:
            self.scale = s
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.color("crit"))
        r = 3.5 * self.scale
        p.drawEllipse(QRectF(7 - r, 7 - r, 2 * r, 2 * r))


class Tile(QWidget):
    """One vital: caption, big mono number with a unit, small subtitle."""

    def __init__(self, caption: str, unit: str = "", px: int = 24, sub: str = ""):
        super().__init__()
        self.unit, self.px = unit, px
        col = QVBoxLayout(self)
        col.setContentsMargins(12, 8, 12, 8)
        col.setSpacing(3)
        self.cap_row = QHBoxLayout()
        self.cap_row.setContentsMargins(0, 0, 0, 0)
        self.cap_row.setSpacing(0)
        self.cap_row.addWidget(cap_label(caption))
        self.cap_row.addStretch(1)
        col.addLayout(self.cap_row)
        # Number and unit are separate labels so each keeps its own font; the
        # unit sits on the number's baseline via bottom alignment.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.value = QLabel()
        self.value.setFont(theme.mono_font(px, QFont.Medium))
        row.addWidget(self.value, 0, Qt.AlignBottom)
        if unit:
            u = QLabel(unit)
            u.setFont(theme.ui_font(10.5, QFont.DemiBold))
            u.setProperty("tone", "3")
            u.setContentsMargins(0, 0, 0, round(px * 0.12))  # descender gap
            row.addWidget(u, 0, Qt.AlignBottom)
        row.addStretch(1)
        col.addLayout(row)
        self.sub = ElidedLabel(sub, 11, tone="3")
        col.addWidget(self.sub)
        col.addStretch(1)
        self.set("–")

    def set(self, text: str, tone: str | None = None) -> None:
        if text != self.value.text():
            self.value.setText(text)
        set_tone(self.value, tone)

    def set_sub(self, text: str) -> None:
        if text != self.sub.text():
            self.sub.setText(text)


class Vitals(QFrame):
    def __init__(self, reduce_motion: bool):
        super().__init__()
        self.setObjectName("box")
        self.hr = Tile("Heart rate", "BPM", 30, "8-beat mean")
        self.heart = Heart(reduce_motion)
        self.hr.cap_row.insertWidget(0, self.heart)
        self.hrv = Tile("HRV · RMSSD", "MS", 30, "15 s window")
        self.d_hr = Tile("HR change", sub="vs opening silence")
        self.d_hrv = Tile("HRV change", sub="vs opening silence")
        self.quality = Tile("Signal", px=17, sub="Artifact beats, 60 s")
        self.beats = Tile("Beats", px=17, sub="Recorded")
        self.tiles = [
            self.hr,
            self.hrv,
            self.d_hr,
            self.d_hrv,
            self.quality,
            self.beats,
        ]
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(1, 1, 1, 1)
        self._grid.setSpacing(0)
        self._rules: list[QFrame] = []
        self.set_compact(False)

    def set_compact(self, compact: bool) -> None:
        """Six columns when wide; two rows of three when narrow."""
        for r in self._rules:
            r.deleteLater()
        self._rules = []
        for t in self.tiles:
            self._grid.removeWidget(t)
        for c in range(11):
            self._grid.setColumnStretch(c, 0)
        cols = 3 if compact else 6
        weights = [12, 12, 10, 10, 13, 9]
        for i, t in enumerate(self.tiles):
            row, col = divmod(i, cols)
            self._grid.addWidget(t, row * 2, col * 2)
            self._grid.setColumnStretch(col * 2, 10 if compact else weights[i])
            if col:
                self._grid.addWidget(self._rule(vertical=True), row * 2, col * 2 - 1)
        if compact:
            self._grid.addWidget(self._rule(vertical=False), 1, 0, 1, cols * 2 - 1)

    def _rule(self, vertical: bool) -> QFrame:
        r = QFrame()
        r.setObjectName("vrule")
        if vertical:
            r.setFixedWidth(1)
        else:
            r.setFixedHeight(1)
        self._rules.append(r)
        return r

    def refresh(self, s, now: float, connected: bool, hrv_win: int, ref) -> None:
        """Recompute every tile from the session (2 Hz)."""
        live = connected and s.beats and now - s.beats[-1].t < hrv.NO_BEATS_AFTER_S
        hr = hrv.hr_now(s.beats) if live else None
        rm = s.rmssd(now - hrv_win, now) if live else None
        self.hr.set(str(round(hr)) if hr else "–")
        self.hrv.set(str(round(rm)) if rm else "–")
        self.hrv.set_sub(f"{hrv_win} s window")

        # % change against the opening silence, once it has closed with 30 beats.
        base = ref if ref and ref["ok"] and not ref["open"] else None
        self._delta(self.d_hr, hr, base and base["hr"], lower_is_calmer=True)
        self._delta(self.d_hrv, rm, base and base["rmssd"], lower_is_calmer=False)
        if not ref:
            sub = None
        elif ref["open"]:
            sub = "Opening silence in progress"
        elif not ref["ok"]:
            sub = "Opening silence under 30 beats"
        else:
            sub = ""
        if sub is None or sub:
            self.d_hr.set_sub(sub or "vs opening silence")
            self.d_hrv.set_sub(sub or "vs opening silence")
        else:
            self.d_hr.set_sub(f"vs opening silence, {round(ref['hr'])} bpm")
            r = f"{round(ref['rmssd'])} ms" if ref["rmssd"] else "–"
            self.d_hrv.set_sub(f"vs opening silence, {r}")

        if not connected:
            self.quality.set("–")
            self.quality.set_sub("Artifact beats, 60 s")
        elif not live:
            self.quality.set("No beats", "crit")
            self.quality.set_sub("Check strap contact")
        else:
            a = hrv.artifact_percent(
                hrv.beats_in(s.beats, now - hrv.QUALITY_WINDOW_S, now)
            )
            grade = hrv.quality_grade(a)
            tone = {"Good": "ok", "Fair": "warn", "Poor": "crit"}[grade]
            self.quality.set(f"{grade} · {a:.1f}%", tone)
            self.quality.set_sub("Artifact beats, 60 s")

        n = sum(b.rec for b in s.beats) if s.phase != "idle" else None
        self.beats.set(f"{n:,}" if n is not None else "–")

    @staticmethod
    def _delta(tile: Tile, cur, ref, lower_is_calmer: bool) -> None:
        p = hrv.pct_change(cur, ref)
        if p is None:
            tile.set("–")
            return
        tone = None if abs(p) < 1 else "ok" if (p < 0) == lower_is_calmer else "warn"
        tile.set(f"{'+' if p > 0 else ''}{p:.1f}%", tone)
