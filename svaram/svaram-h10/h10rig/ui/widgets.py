"""Small shared widgets: painted buttons, pills, segmented controls, boxes.

Buttons and pills are painted by hand rather than styled with QSS so they can
carry a keyboard hint (kbd), a hold-to-stop progress fill and a blinking dot,
and look the same on every platform.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme

HOLD_MS = 1200  # press-and-hold time for Stop


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------


def clock_text(seconds: float) -> str:
    """mm:ss, or h:mm:ss from one hour."""
    s = max(0, int(seconds))
    h, m, x = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m:02d}:{x:02d}"


def text_label(text="", px=13, weight=QFont.Normal, tone=None, mono=False) -> QLabel:
    """A QLabel with a font size and an optional colour tone ("2", "3", "ok"...)."""
    w = QLabel(text)
    w.setFont(theme.mono_font(px, weight) if mono else theme.ui_font(px, weight))
    if tone:
        w.setProperty("tone", tone)
    return w


def cap_label(text="") -> QLabel:
    """Uppercase caption, as used above every vital and palette family."""
    w = QLabel(text)
    w.setFont(theme.cap_font())
    w.setProperty("tone", "3")
    return w


class ElidedLabel(QLabel):
    """Single-line label that ends in "…" instead of forcing the layout wider."""

    def __init__(self, text="", px=12, mono=False, tone=None):
        super().__init__(text)
        self.setFont(theme.mono_font(px) if mono else theme.ui_font(px))
        if tone:
            self.setProperty("tone", tone)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(20, super().minimumSizeHint().height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        p.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, text)


def set_tone(widget: QWidget, tone: str | None) -> None:
    """Change a label's colour tone and re-apply the stylesheet to it."""
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def draw_kbd(p: QPainter, rect: QRectF, key: str, fg, bg, border, alpha=1.0):
    """Keycap hint: rounded box with a heavier bottom edge."""
    p.save()
    p.setOpacity(p.opacity() * alpha)
    if bg is not None:  # border colour underneath, face inset 1 px (2 px below)
        p.setPen(Qt.NoPen)
        p.setBrush(border)
        p.drawRoundedRect(rect, 4, 4)
        p.setBrush(bg)
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -2), 3, 3)
    else:  # transparent keycap on a filled button: draw the outline only
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
    p.setPen(fg)
    p.setFont(theme.mono_font(10, QFont.DemiBold))
    p.drawText(rect.adjusted(0, 0, 0, -1), Qt.AlignCenter, key)
    p.restore()


def kbd_width(key: str) -> float:
    return max(
        17.0,
        QFontMetricsF(theme.mono_font(10, QFont.DemiBold)).horizontalAdvance(key) + 8,
    )


def fast_polyline(p: QPainter, poly, color, width: float) -> None:
    """Draw a thick antialiased polyline quickly.

    Qt's raster engine has a fast path only for lines at most 1 physical pixel
    wide; anything wider goes through the general stroker, which costs ~10-20 ms
    for a 650-point ECG on a Retina screen. Drawing a 1 px cosmetic line nine
    times, offset in a small plus pattern, covers the same width in ~1 ms and
    looks the same at these sizes. `width` is in logical pixels.
    """
    dpr = p.device().devicePixelRatioF()
    r = max(0.0, (width * 0.85 * dpr - 1) / 2) / dpr  # offset radius, logical px
    pen = QPen(color, 1)
    pen.setCosmetic(True)
    p.setPen(pen)
    p.drawPolyline(poly)
    if r < 0.1:
        return
    for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)):
        for f in (0.5, 1.0):
            p.drawPolyline(poly.translated(dx * f, dy * f))


def mix(a, b, t: float):
    """Blend colour a towards b by t (0..1); used instead of translucency."""
    out = type(a)(a)
    out.setRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
    )
    return out


# --------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------


class Button(QAbstractButton):
    """Painted push button.

    variant: "default" | "primary" | "ghost" | "hold"
    key:     optional keyboard hint drawn as a keycap after the text
    large:   36 px tall instead of 30
    """

    def __init__(self, text, variant="default", key=None, large=False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.variant = variant
        self.key = key
        self.large = large
        self.progress = 0.0  # hold-to-stop fill, 0..1
        self._keyboard_focus = False  # focus ring only when tabbed to
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)  # clicks don't leave a focus ring
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _font(self) -> QFont:
        return theme.ui_font(13 if self.large else 12.5, QFont.DemiBold)

    def focusInEvent(self, e):
        self._keyboard_focus = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(e)

    def sizeHint(self) -> QSize:
        pad = 16 if self.large else 11
        w = QFontMetricsF(self._font()).horizontalAdvance(self.text()) + 2 * pad
        if self.key:
            w += 7 + kbd_width(self.key)
        return QSize(math.ceil(w), 36 if self.large else 30)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = self.underMouse() and self.isEnabled()
        c = theme.color
        if self.variant == "primary":
            bg = c("primary").lighter(115) if hover else c("primary")
            border, fg = bg, c("on-primary")
        elif self.variant == "ghost":
            bg = c("surface-2") if hover else None
            border, fg = None, c("ink-2")
        elif self.variant == "hold":
            bg = c("surface-2") if hover else c("surface")
            border, fg = c("crit"), c("crit")
        else:
            bg = c("surface-2") if hover or self.isDown() else c("surface")
            border, fg = c("line-2"), c("ink")

        p.setPen(QPen(border, 1) if border is not None else Qt.NoPen)
        p.setBrush(bg if bg is not None else Qt.NoBrush)
        p.drawRoundedRect(r, 6, 6)

        if self.progress > 0:  # hold fill grows from the left
            fill = QRectF(r.left(), r.top(), r.width() * self.progress, r.height())
            p.save()
            p.setClipRect(fill)
            p.setPen(Qt.NoPen)
            col = c("crit")
            col.setAlphaF(0.2)
            p.setBrush(col)
            p.drawRoundedRect(r, 6, 6)
            p.restore()

        if self.hasFocus() and self._keyboard_focus:
            p.setPen(QPen(c("accent"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), 6, 6)

        font = self._font()
        fm = QFontMetricsF(font)
        tw = fm.horizontalAdvance(self.text())
        kw = kbd_width(self.key) if self.key else 0
        total = tw + (7 + kw if self.key else 0)
        x = (self.width() - total) / 2
        p.setFont(font)
        p.setPen(fg)
        p.drawText(QRectF(x, 0, tw + 1, self.height()), Qt.AlignVCenter, self.text())
        if self.key:
            kr = QRectF(x + tw + 7, (self.height() - 17) / 2, kw, 17)
            if self.variant == "primary":
                draw_kbd(p, kr, self.key, fg, None, fg, 0.7)
            else:
                draw_kbd(p, kr, self.key, c("ink-2"), c("surface"), c("line-2"))


class HoldButton(Button):
    """Stop button that fires `held` only after 1.2 s of continuous press.

    Works with the pointer and with Space/Enter. Releasing early, or moving the
    pointer off the button, cancels without side effects.
    """

    held = Signal()

    def __init__(self, text, parent=None):
        super().__init__(text, "hold", parent=parent)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(HOLD_MS)
        self._anim.setEasingCurve(QEasingCurve.Linear)
        self._anim.valueChanged.connect(self._on_progress)
        self._anim.finished.connect(self._on_done)

    def _on_progress(self, v):
        self.progress = float(v)
        self.update()

    def _on_done(self):
        self.progress = 0.0
        self.update()
        self.held.emit()

    def arm(self):
        if self._anim.state() != QVariantAnimation.Running:
            self._anim.start()

    def disarm(self):
        self._anim.stop()
        self.progress = 0.0
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.arm()

    def mouseReleaseEvent(self, e):
        self.disarm()

    def leaveEvent(self, e):
        self.disarm()
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            if not e.isAutoRepeat():
                self.arm()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            if not e.isAutoRepeat():
                self.disarm()
            return
        super().keyReleaseEvent(e)

    def hideEvent(self, e):
        self.disarm()
        super().hideEvent(e)


# --------------------------------------------------------------------------
# Pill
# --------------------------------------------------------------------------


class Pill(QWidget):
    """Rounded status chip with a coloured dot.

    tone: "" | "ok" | "warn" | "rec" | "good". `dot_alpha` lets the window blink
    the recording dot without re-laying out anything.
    """

    def __init__(self, text="", tone="", parent=None):
        super().__init__(parent)
        self._text, self.tone, self.dot_alpha = text, tone, 1.0
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set(self, text: str, tone: str = "") -> None:
        if (text, tone) != (self._text, self.tone):
            changed_width = text != self._text
            self._text, self.tone = text, tone
            if changed_width:
                self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(theme.ui_font(12, QFont.DemiBold))
        return QSize(math.ceil(fm.horizontalAdvance(self._text) + 9 + 7 + 6 + 9), 24)

    def paintEvent(self, _):
        c = theme.color
        bg, border, fg, dot = c("surface-2"), c("line"), c("ink-2"), c("ink-3")
        if self.tone == "ok":
            dot = c("ok")
        elif self.tone == "warn":
            dot = c("warn")
        elif self.tone == "rec":
            bg, border, fg, dot = c("crit-bg"), None, c("crit"), c("crit")
        elif self.tone == "good":
            bg, border, fg, dot = c("ok-bg"), None, c("ok"), c("ok")
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(border, 1) if border is not None else Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 12, 12)
        p.setPen(Qt.NoPen)
        p.setOpacity(self.dot_alpha if self.tone == "rec" else 1.0)
        p.setBrush(dot)
        p.drawEllipse(QRectF(9, self.height() / 2 - 3.5, 7, 7))
        p.setOpacity(1.0)
        p.setPen(fg)
        p.setFont(theme.ui_font(12, QFont.DemiBold))
        p.drawText(
            QRectF(22, 0, self.width() - 22, self.height()), Qt.AlignVCenter, self._text
        )


# --------------------------------------------------------------------------
# Segmented control and boxes
# --------------------------------------------------------------------------


class Segmented(QFrame):
    """Caption plus exclusive buttons, e.g. "HRV | 15 s | 30 s | 45 s"."""

    changed = Signal(int)

    def __init__(self, caption: str, options: list[tuple[str, int]], value: int):
        super().__init__()
        self.setObjectName("seg")
        self.setFixedHeight(24)
        row = QHBoxLayout(self)
        row.setContentsMargins(1, 1, 1, 1)
        row.setSpacing(0)
        cap = QLabel(caption)
        cap.setAlignment(Qt.AlignVCenter)
        row.addWidget(cap)
        group = QButtonGroup(self)
        for i, (text, v) in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(v == value)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.TabFocus)
            b.setProperty("mid", i > 0)
            b.setProperty("last", i == len(options) - 1)
            b.clicked.connect(lambda _=False, v=v: self.changed.emit(v))
            group.addButton(b)
            row.addWidget(b)


def box(
    title: str | None = None, frame: QFrame | None = None
) -> tuple[QFrame, QHBoxLayout | None, QVBoxLayout]:
    """Bordered surface panel with an optional header row.

    Builds into `frame` if given (for QFrame subclasses), else a new QFrame.
    Returns (frame, header_layout or None, body_layout).
    """
    frame = frame or QFrame()
    frame.setObjectName("box")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(1, 1, 1, 1)
    outer.setSpacing(0)
    head_layout = None
    if title is not None:
        head = QFrame()
        head.setObjectName("boxHead")
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(12, 6, 12, 6)
        head_layout.setSpacing(10)
        head_layout.addWidget(text_label(title, 12.5, QFont.Bold))
        head_layout.addStretch(1)
        outer.addWidget(head)
    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(0)
    outer.addLayout(body, 1)
    return frame, head_layout, body
