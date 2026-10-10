"""Right rail: the label palette, grouped by instrument family.

Each key shows its hotkey, name and, while on, how long it has been on.
Clicking or pressing the hotkey toggles it; the session applies the rules
(see session.next_set).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import FAMILIES, LABELS, SILENCE
from . import theme
from .widgets import box, cap_label, clock_text, draw_kbd, kbd_width, text_label


class Swatch(QWidget):
    """8 px rounded square in a family colour."""

    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.setFixedSize(8, 8)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.color(self.token))
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)


class LabelKey(QAbstractButton):
    """One palette key: [kbd] name ......... 01:23"""

    def __init__(self, label: str):
        super().__init__()
        self.label = label
        self.key = LABELS[label]["key"]
        self.token = LABELS[label]["color"]
        self.on = False
        self.elapsed = ""
        self.setFixedHeight(32)
        self.setMinimumWidth(60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)  # keyboard users have the hotkeys
        self.setAttribute(Qt.WA_Hover)
        self.setToolTip(f"{label} ({self.key})")
        self.f_name = theme.ui_font(12, QFont.DemiBold)
        self.f_time = theme.mono_font(10.5, QFont.Medium)

    def sizeHint(self) -> QSize:
        return QSize(130, 32)

    def set_state(self, on: bool, elapsed: str) -> None:
        if (on, elapsed) != (self.on, self.elapsed):
            self.on, self.elapsed = on, elapsed
            self.update()

    def paintEvent(self, _):
        c = theme.color
        col = c(self.token)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.5)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = self.underMouse() and self.isEnabled()
        if self.on:
            p.setPen(QPen(col, 1))
            p.setBrush(col)
            fg = c("on-label")
        else:
            p.setPen(QPen(col if hover else c("line"), 1))
            p.setBrush(c("surface"))
            fg = c("ink")
        p.drawRoundedRect(r, 6, 6)

        kw = kbd_width(self.key)
        kr = QRectF(5, (self.height() - 17) / 2, kw, 17)
        if self.on:
            draw_kbd(p, kr, self.key, fg, None, fg, 0.8)
        else:
            draw_kbd(p, kr, self.key, c("ink-2"), c("surface"), c("line-2"))

        tw = 0.0
        if self.elapsed:
            tw = QFontMetricsF(self.f_time).horizontalAdvance(self.elapsed)
            p.setFont(self.f_time)
            p.setPen(fg)
            p.setOpacity(p.opacity() * 0.85)
            p.drawText(
                QRectF(self.width() - 7 - tw, 0, tw + 1, self.height()),
                Qt.AlignVCenter,
                self.elapsed,
            )
            p.setOpacity(1.0 if self.isEnabled() else 0.5)
        x = kr.right() + 7
        avail = self.width() - x - 7 - (tw + 7 if tw else 0)
        p.setFont(self.f_name)
        p.setPen(fg)
        name = QFontMetricsF(self.f_name).elidedText(self.label, Qt.ElideRight, avail)
        p.drawText(QRectF(x, 0, avail, self.height()), Qt.AlignVCenter, name)


class LabelRail(QFrame):
    toggled = Signal(str)

    def __init__(self):
        super().__init__()
        _, head, body = box("Active labels", self)
        self.now = text_label("Not recording", 11.5, tone="2")
        head.addWidget(self.now)

        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(10, 8, 10, 10)
        col.setSpacing(9)
        self.keys: list[LabelKey] = []
        for fam in FAMILIES:
            cap = QHBoxLayout()
            cap.setSpacing(6)
            cap.addWidget(Swatch(fam["color"]))
            cap.addWidget(cap_label(fam["name"]))
            cap.addStretch(1)
            col.addLayout(cap)
            grid = QGridLayout()
            grid.setSpacing(4)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for i, (name, _key) in enumerate(fam["items"]):
                k = LabelKey(name)
                k.clicked.connect(lambda _=False, n=name: self.toggled.emit(n))
                grid.addWidget(k, i // 2, i % 2)
                self.keys.append(k)
            col.addLayout(grid)
        note = QLabel(
            "Toggle instruments as they start and stop. Several can be on at "
            "once. With nothing on, the label is Silence."
        )
        note.setWordWrap(True)
        note.setFont(theme.ui_font(11.5))
        note.setProperty("tone", "3")
        col.addWidget(note)
        col.addStretch(1)
        self._content = content

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(content)
        body.addWidget(self.scroll, 1)

    def set_compact(self, compact: bool) -> None:
        """Narrow windows scroll the whole page, so show the rail in full."""
        if compact:
            self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.scroll.setMinimumHeight(self._content.sizeHint().height())
        else:
            self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.scroll.setMinimumHeight(0)

    def refresh(self, s, now: float) -> None:
        live = s.recording
        for k in self.keys:
            on = live and k.label in s.active
            k.setEnabled(live)
            k.set_state(on, clock_text(now - s.since_on(k.label, now)) if on else "")
        n = len(s.active - {SILENCE})
        if not live:
            text = "Stopped" if s.phase == "done" else "Not recording"
        else:
            text = f"{n} active" if n else "Silence"
        if text != self.now.text():
            self.now.setText(text)
