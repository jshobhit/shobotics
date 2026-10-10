"""Title bar: brand, session IDs, sensor state and the recording controls."""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QWidget

from . import theme
from .widgets import Button, ElidedLabel, HoldButton, Pill, text_label

LOGO_FILE = "shobotics-logo.svg"
LOGO_FILL = "#30003f"  # the fill baked into the asset, swapped for the theme
LOGO_VIEW = QRectF(0, 10, 147.69, 38)  # crop the asset's empty top margin
LOGO_HEIGHT = 28


def asset_path(name: str) -> Path:
    """Locate a bundled asset in a PyInstaller build or the source tree."""
    base = getattr(sys, "_MEIPASS", None)  # set inside the built app
    root = Path(base) if base else Path(__file__).resolve().parents[2]
    return root / name


class Logo(QWidget):
    """The Shobotics wordmark, filled with the theme's primary colour."""

    def __init__(self):
        super().__init__()
        try:
            self._svg = asset_path(LOGO_FILE).read_text(encoding="utf-8")
        except OSError:
            self._svg = ""
        self._renderer = None
        width = round(LOGO_HEIGHT * LOGO_VIEW.width() / LOGO_VIEW.height())
        self.setFixedSize(QSize(width, LOGO_HEIGHT))
        self.setAccessibleName("Shobotics")

    def restyle(self):
        """Re-tint after a theme change."""
        fill = theme.css("primary")
        data = self._svg.replace(LOGO_FILL, fill).replace(LOGO_FILL.upper(), fill)
        self._renderer = QSvgRenderer(data.encode("utf-8"))
        self._renderer.setViewBox(LOGO_VIEW)
        self.update()

    def paintEvent(self, _):
        if self._renderer is None:
            self.restyle()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._renderer.render(p, QRectF(self.rect()))


class TitleBar(QFrame):
    """50 px bar. Buttons are exposed as attributes for the window to wire."""

    def __init__(self):
        super().__init__()
        self.setObjectName("bar")

        # Left group: logo | Sonorium, session IDs, document count.
        left = QWidget()
        lrow = QHBoxLayout(left)
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(12)
        self.logo = Logo()
        lrow.addWidget(self.logo)
        rule = QFrame()
        rule.setObjectName("brandRule")
        rule.setFixedSize(1, 22)
        lrow.addWidget(rule)
        lrow.addWidget(text_label("Sonorium", 15, QFont.Bold))
        lrow.addSpacing(2)
        self.ids = ElidedLabel("No session", 12, mono=True, tone="2")
        lrow.addWidget(self.ids)
        self.docs = Pill("", "good")
        self.docs.hide()
        lrow.addWidget(self.docs)
        lrow.addStretch(1)

        # Right group: sensor, connect, recording state, clock, actions.
        right = QWidget()
        rrow = QHBoxLayout(right)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.setSpacing(14)
        self.sensor = Pill("Polar H10 not connected")
        self.connect = Button("Connect Polar H10")
        self.rec = Pill("Idle")
        self.clock = QLabel("00:00")
        self.clock.setFont(theme.mono_font(20, QFont.Medium))
        self.clock.setMinimumWidth(64)
        self.clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.stop = HoldButton("Hold to stop")
        self.review = Button("Review")
        self.new = Button("New recording", "primary", key="N")
        for w in (
            self.sensor,
            self.connect,
            self.rec,
            self.clock,
            self.stop,
            self.review,
            self.new,
        ):
            rrow.addWidget(w)
        self.stop.hide()
        self.review.hide()

        self._row = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(14)
        self._row.addWidget(left, 1)
        self._row.addWidget(right, 0)
        self.set_compact(False)

    def set_compact(self, compact: bool) -> None:
        """Narrow windows: put the controls on a second row."""
        if compact:
            self._row.setDirection(QBoxLayout.TopToBottom)
            self._row.setContentsMargins(0, 8, 0, 8)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        else:
            self._row.setDirection(QBoxLayout.LeftToRight)
            self._row.setContentsMargins(0, 0, 0, 0)
            self.setFixedHeight(50)

    def show_session(self, ids: dict, docs: dict) -> None:
        if ids["p"]:
            text = " · ".join((ids["p"], ids["s"], ids["f"]))
            self.ids.setText(text)
            self.ids.setToolTip(text)
            n_c, n_q = len(docs["consent"]), len(docs["questionnaire"])
            self.docs.set(f"Consent {n_c} · Questionnaire {n_q}", "good")
            self.docs.show()
        else:
            self.ids.setText("No session")
            self.docs.hide()
