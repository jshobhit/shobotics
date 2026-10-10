"""Colour tokens, fonts and the Qt stylesheet for light and dark mode.

Token names and values come straight from the Sonorium reference (`--bg`,
`--surface`, ...). Painted widgets read colours through `color(name)`; regular
widgets get them through the stylesheet. `apply()` re-reads the system colour
scheme, so the app follows macOS light/dark switching live.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

LIGHT = {
    "bg": "#F2EDE7", "surface": "#FFFDF8", "surface-2": "#F6F1EA",
    "line": "#E1D8CF", "line-2": "#CFC4BA",
    "ink": "#2C2132", "ink-2": "#5A4E5D", "ink-3": "#8A7F8B",
    "primary": "#30003F", "on-primary": "#FFFDF8", "accent": "#6E4A78",
    "ok": "#3E7B4C", "warn": "#A86A12", "crit": "#B23A3A",
    "ok-bg": "#E2EDD8", "warn-bg": "#F7E8D0", "crit-bg": "#F5DCD8",
    "trace": "#30003F",
    "paper": "rgba(214,120,110,.15)", "paper-major": "rgba(214,120,110,.30)",
    "f-silence": "#A79CA4", "f-metal": "#B98328", "f-drum": "#A65A44",
    "f-earth": "#3F8791", "f-breath": "#6F934A",
    "on-label": "#FFFFFF",
    "backdrop": "rgba(18,8,24,.42)",
}  # fmt: skip

DARK = {
    "bg": "#151018", "surface": "#1F1823", "surface-2": "#281F2D",
    "line": "#372C3D", "line-2": "#4A3D51",
    "ink": "#F2EAEE", "ink-2": "#C8BCC6", "ink-3": "#94889A",
    "primary": "#E2C6EC", "on-primary": "#24002F", "accent": "#C9A3D8",
    "ok": "#8FCB98", "warn": "#E7B55E", "crit": "#F08F86",
    "ok-bg": "#22301F", "warn-bg": "#382A15", "crit-bg": "#3C1F1F",
    "trace": "#F3C1B9",
    "paper": "rgba(240,179,171,.07)", "paper-major": "rgba(240,179,171,.16)",
    "f-silence": "#6E6373", "f-metal": "#E0AE55", "f-drum": "#D9846C",
    "f-earth": "#6EB8C2", "f-breath": "#9CC274",
    "on-label": "#160E1A",
    "backdrop": "rgba(18,8,24,.42)",
}  # fmt: skip

MONO_FAMILIES = ["Menlo", "Cascadia Mono", "Consolas", "DejaVu Sans Mono"]

_tokens = LIGHT
_colors: dict[str, QColor] = {}


def is_dark() -> bool:
    return _tokens is DARK


def apply(app) -> None:
    """Pick tokens from the system colour scheme and restyle the app."""
    global _tokens
    dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    _tokens = DARK if dark else LIGHT
    _colors.clear()
    # The palette styles what the stylesheet does not reach (message boxes,
    # file dialogs, menus); the stylesheet styles the app's own widgets.
    pal = QPalette()
    for role, name in (
        (QPalette.Window, "surface"),
        (QPalette.WindowText, "ink"),
        (QPalette.Base, "surface"),
        (QPalette.AlternateBase, "surface-2"),
        (QPalette.Text, "ink"),
        (QPalette.Button, "surface-2"),
        (QPalette.ButtonText, "ink"),
        (QPalette.Highlight, "accent"),
        (QPalette.HighlightedText, "on-primary"),
        (QPalette.PlaceholderText, "ink-3"),
        (QPalette.Mid, "line-2"),
    ):
        pal.setColor(role, color(name))
    app.setPalette(pal)
    font = app.font()
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(stylesheet())


def color(name: str) -> QColor:
    """Token as a QColor. Returns a copy, so callers may change its alpha."""
    if name not in _colors:
        _colors[name] = _parse(_tokens[name])
    return QColor(_colors[name])


def css(name: str) -> str:
    """Token as a stylesheet colour string."""
    return _tokens[name]


def ui_font(px: float, weight: int = QFont.Normal) -> QFont:
    """System UI font (San Francisco on macOS) at a pixel size."""
    f = QFont(QGuiApplication.font())
    f.setPixelSize(round(px))
    f.setWeight(QFont.Weight(weight))
    return f


def mono_font(px: float, weight: int = QFont.Normal) -> QFont:
    """System monospace font for numbers, so digits keep their width."""
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setStyleHint(QFont.Monospace)
    f.setPixelSize(round(px))
    f.setWeight(QFont.Weight(weight))
    return f


def cap_font() -> QFont:
    """Small uppercase caption font (10.5 px, semibold, tracked)."""
    f = ui_font(10.5, QFont.DemiBold)
    f.setCapitalization(QFont.AllUppercase)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 0.7)
    return f


def _parse(value: str) -> QColor:
    if value.startswith("rgba("):
        r, g, b, a = (float(x) for x in value[5:-1].split(","))
        return QColor(int(r), int(g), int(b), round(a * 255))
    return QColor(value)


def stylesheet() -> str:
    t = _tokens
    mono = ", ".join(f'"{f}"' for f in MONO_FAMILIES)
    return f"""
    QWidget#root, QMainWindow {{ background: {t['bg']}; }}
    QWidget {{ color: {t['ink']}; }}
    QLabel {{ background: transparent; }}
    QLabel[tone="2"] {{ color: {t['ink-2']}; }}
    QLabel[tone="3"] {{ color: {t['ink-3']}; }}
    QLabel[tone="ok"] {{ color: {t['ok']}; }}
    QLabel[tone="warn"] {{ color: {t['warn']}; }}
    QLabel[tone="crit"] {{ color: {t['crit']}; }}

    QFrame#bar {{ border: 0; border-bottom: 1px solid {t['line']}; }}
    QFrame#brandRule {{ background: {t['line']}; }}
    QFrame#box {{ background: {t['surface']}; border: 1px solid {t['line']};
                  border-radius: 8px; }}
    QFrame#boxHead {{ border: 0; border-bottom: 1px solid {t['line']};
                      background: transparent; }}
    QFrame#dialogFoot {{ border: 0; border-top: 1px solid {t['line']};
                         background: transparent; }}
    QFrame#vrule {{ background: {t['line']}; }}
    QFrame#alert {{ background: {t['crit-bg']}; border-radius: 8px; }}
    QFrame#alert QLabel {{ color: {t['crit']}; font-weight: 600; }}

    QFrame#seg {{ border: 1px solid {t['line-2']}; border-radius: 6px;
                  background: {t['surface']}; }}
    QFrame#seg QLabel {{ background: {t['surface-2']}; color: {t['ink-3']};
                         font-size: 11px; font-weight: 600; padding: 0 8px;
                         border-right: 1px solid {t['line-2']};
                         border-top-left-radius: 5px; border-bottom-left-radius: 5px; }}
    QFrame#seg QPushButton {{ border: 0; background: transparent; color: {t['ink-2']};
                              font-size: 11.5px; font-weight: 600; padding: 0 9px;
                              min-height: 22px; max-height: 22px; }}
    QFrame#seg QPushButton[mid="true"] {{ border-left: 1px solid {t['line-2']}; }}
    QFrame#seg QPushButton:checked {{ background: {t['primary']}; color: {t['on-primary']}; }}
    QFrame#seg QPushButton[last="true"] {{ border-top-right-radius: 5px;
                                           border-bottom-right-radius: 5px; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['line-2']}; border-radius: 3px;
                                   min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QDialog {{ background: {t['surface']}; }}
    QFrame#doc {{ border: 1px dashed {t['line-2']}; border-radius: 6px; }}
    QFrame#doc[has="true"] {{ border: 1px solid {t['line']}; background: {t['surface-2']}; }}
    QFrame#doc[drag="true"] {{ border: 1px dashed {t['accent']}; background: {t['surface-2']}; }}
    QLabel#req {{ font-size: 10.5px; font-weight: 700; padding: 3px 7px;
                  border-radius: 9px; background: {t['warn-bg']}; color: {t['warn']}; }}
    QLabel#req[ok="true"] {{ background: {t['ok-bg']}; color: {t['ok']}; }}
    QPushButton#remove {{ border: 0; background: transparent; color: {t['ink-3']};
                          font-size: 11px; font-weight: 600; padding: 0 2px; }}
    QPushButton#remove:hover {{ color: {t['crit']}; }}

    QLineEdit {{ min-height: 30px; border-radius: 6px; border: 1px solid {t['line-2']};
                 background: {t['surface']}; padding: 0 9px; font-family: {mono};
                 selection-background-color: {t['accent']}; }}
    QLineEdit:focus {{ border: 2px solid {t['accent']}; padding: 0 8px; }}
    QLineEdit[auto="true"] {{ border-color: {t['ok']}; background: {t['ok-bg']}; }}

    QFrame#stats {{ background: {t['line']}; border: 1px solid {t['line']};
                    border-radius: 6px; }}
    QFrame#stat {{ background: {t['surface']}; }}
    QTableWidget {{ background: {t['surface']}; border: 1px solid {t['line']};
                    border-radius: 6px; gridline-color: transparent; font-size: 12px;
                    selection-background-color: {t['surface-2']};
                    selection-color: {t['ink']}; }}
    QTableWidget::item {{ border-bottom: 1px solid {t['line']}; padding: 0 9px; }}
    QHeaderView::section {{ background: {t['surface-2']}; color: {t['ink-3']};
                            border: 0; border-bottom: 1px solid {t['line']};
                            padding: 5px 9px; font-size: 10.5px; font-weight: 600; }}
    QToolTip {{ background: {t['surface']}; color: {t['ink']};
                border: 1px solid {t['line-2']}; padding: 4px 6px; }}
    QMessageBox {{ background: {t['surface']}; }}
    """
