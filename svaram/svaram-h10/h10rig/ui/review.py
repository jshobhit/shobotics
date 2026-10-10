"""Review dialog: session summary, per-label table and exports."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import hrv
from . import theme
from .trend_chart import label_color
from .widgets import Button, cap_label, clock_text, text_label

COLUMNS = ("Label", "Start", "Duration", "Mean HR", "RMSSD", "ΔHR", "ΔRMSSD")
EXPORTS = (("csv", "Beats CSV"), ("ecg", "ECG CSV"), ("json", "JSON"))


def swatch(color) -> QIcon:
    pm = QPixmap(8, 8)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(0, 0, 8, 8), 2, 2)
    p.end()
    return QIcon(pm)


class ReviewDialog(QDialog):
    export_requested = Signal(str)  # "csv" | "ecg" | "json" | "zip"

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Session review")
        self.setModal(True)
        self.setFixedWidth(860)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        head = QFrame()
        head.setObjectName("boxHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(10)
        h.addWidget(text_label("Session review", 15, QFont.Bold))
        self.ids = text_label("", 13, tone="2", mono=True)
        h.addWidget(self.ids)
        h.addStretch(1)
        close = Button("Close", "ghost", key="Esc")
        close.clicked.connect(self.reject)
        h.addWidget(close)
        col.addWidget(head)

        body = QVBoxLayout()
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(12)
        stats = QFrame()
        stats.setObjectName("stats")
        grid = QGridLayout(stats)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)
        self.stat = {}
        for i, (key, caption) in enumerate(
            (("dur", "Duration"), ("beats", "Beats"), ("art", "Artifact beats"),
             ("ref", "Opening silence"))
        ):  # fmt: skip
            cell = QFrame()
            cell.setObjectName("stat")
            v = QVBoxLayout(cell)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(2)
            v.addWidget(cap_label(caption))
            value = text_label("", 16, mono=True)
            v.addWidget(value)
            grid.addWidget(cell, 0, i)
            grid.setColumnStretch(i, 1)
            self.stat[key] = value
        body.addWidget(stats)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(26)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(COLUMNS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            item = self.table.horizontalHeaderItem(i)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.table.horizontalHeaderItem(0).setTextAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        self.table.setMaximumHeight(300)
        body.addWidget(self.table)
        self.note = text_label("", 12, tone="2")
        self.note.setMinimumHeight(17)
        body.addWidget(self.note)
        col.addLayout(body)

        foot = QFrame()
        foot.setObjectName("dialogFoot")
        f = QHBoxLayout(foot)
        f.setContentsMargins(16, 10, 16, 10)
        f.setSpacing(10)
        hint = QLabel(
            "Package holds beats.csv, ecg.csv, session.json and the attached documents."
        )
        hint.setWordWrap(True)
        hint.setFont(theme.ui_font(12))
        hint.setProperty("tone", "3")
        f.addWidget(hint, 1)
        for kind, text in EXPORTS:
            b = Button(text)
            b.clicked.connect(lambda _=False, k=kind: self.export_requested.emit(k))
            f.addWidget(b)
        pkg = Button("Save session package", "primary", large=True)
        pkg.clicked.connect(lambda: self.export_requested.emit("zip"))
        f.addWidget(pkg)
        col.addWidget(foot)

    def load(self, s) -> None:
        """Fill the summary from a stopped session."""
        rec = s.recorded(s.beats)
        ref = s.reference(s.t_end)
        self.ids.setText(" · ".join((s.ids["p"], s.ids["s"], s.ids["f"])))
        self.stat["dur"].setText(clock_text(s.rel(s.t_end)))
        self.stat["beats"].setText(f"{len(rec):,}")
        self.stat["art"].setText(f"{hrv.artifact_percent(rec):.1f} %" if rec else "–")
        if ref and ref["ok"]:
            rm = f"{round(ref['rmssd'])} ms" if ref["rmssd"] else "–"
            self.stat["ref"].setText(f"{round(ref['hr'])} bpm · {rm}")
        else:
            self.stat["ref"].setText("Under 30 beats")

        rows = s.segment_stats(s.t_end)
        self.table.setRowCount(len(rows))
        mono = theme.mono_font(12)
        for r, x in enumerate(rows):
            name = QTableWidgetItem(swatch(label_color(x["label"])), x["label"])
            self.table.setItem(r, 0, name)
            cells = (
                clock_text(s.rel(x["t1"])),
                clock_text(x["t2"] - x["t1"]),
                f"{x['hr']:.1f}" if x["hr"] else "–",
                f"{x['rmssd']:.1f}" if x["rmssd"] else "–",
            )
            for c, text in enumerate(cells, start=1):
                self.table.setItem(r, c, self._num(text, mono))
            self.table.setItem(r, 5, self._delta(x["d_hr"], mono, lower_is_calmer=True))
            self.table.setItem(
                r, 6, self._delta(x["d_rmssd"], mono, lower_is_calmer=False)
            )
        self.set_note("" if s.exported else "Not saved yet.")

    def set_note(self, text: str) -> None:
        self.note.setText(text)

    @staticmethod
    def _num(text: str, font) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setFont(font)
        return item

    def _delta(self, v, font, lower_is_calmer: bool) -> QTableWidgetItem:
        if v is None:
            return self._num("–", font)
        item = self._num(f"{'+' if v > 0 else ''}{v:.1f}%", font)
        if abs(v) >= 1:
            good = (v < 0) == lower_is_calmer
            item.setForeground(theme.color("ok" if good else "warn"))
        return item
