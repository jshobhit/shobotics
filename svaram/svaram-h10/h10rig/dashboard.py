"""Native, offline session dashboard hosted in an independent analysis window."""

import math

import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QTabWidget,
    QScrollArea,
    QDoubleSpinBox,
    QSlider,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QToolTip,
)

from .analysis import rr_metrics
from .labels import SOUND_COLORS

GREEN = "#337859"
BLUE = "#4d7891"
AMBER = "#b67f3a"
INK = "#233d36"


def elapsed(t):
    t = max(0, int(t))
    return f"{t//60:02d}:{t%60:02d}"


def number(value, decimals=1):
    return "—" if value is None else f"{value:,.{decimals}f}"


def text(value, style=None):
    widget = QLabel(value)
    if style:
        widget.setObjectName(style)
    widget.setWordWrap(True)
    return widget


def card(title):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 15, 18, 15)
    layout.setSpacing(8)
    layout.addWidget(text(title, "eyebrow"))
    return frame, layout


class Chart(QWidget):
    """Draw named (label, color, points) series while leaving gaps visible.

    Each point is an (x, y) pair; None breaks a line. Dense traces retain
    local extrema for display, while hover values use source observations.
    """

    def __init__(self, unit="", time_axis=True, scatter=False):
        super().__init__()
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        self.unit = unit
        self.time_axis = time_axis
        self.scatter = scatter
        self.series = []
        self.xrange = (0, 1)
        self.gap = 3
        self.empty = "No usable observations in this range"
        self.cursor = None
        self.sound_intervals = []

    def set_data(self, series, xrange, gap=3):
        """Replace the series and visible x range, then request a repaint."""
        self.series = series
        self.xrange = xrange
        self.gap = gap
        self.cursor = None
        self.update()

    def plot_rect(self):
        """Reserve space around the plot for axis labels."""
        return QRectF(58, 18, max(1, self.width() - 82), max(1, self.height() - 63))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        plot_area = self.plot_rect()
        x_min, x_max = self.xrange
        x_span = max(x_max - x_min, 1e-9)
        # A compact state strip remains visible even where sensor data is absent.
        for start, end, value in self.sound_intervals:
            a, b = max(start, x_min), min(end, x_max)
            if a >= b:
                continue
            left = plot_area.left() + (a - x_min) / x_span * plot_area.width()
            width = (b - a) / x_span * plot_area.width()
            painter.fillRect(QRectF(left, 0, width, 14), QColor(SOUND_COLORS[value]))
            painter.setPen(QColor("#ffffff"))
            if width > 65:
                painter.drawText(QRectF(left + 3, 0, width - 6, 14), Qt.AlignLeft | Qt.AlignVCenter, value.title())
        values = [
            y
            for _, _, points in self.series
            for x, y in points
            if y is not None and math.isfinite(y) and x_min <= x <= x_max
        ]
        if not values:
            painter.setPen(QColor("#839088"))
            painter.drawText(plot_area, Qt.AlignCenter, self.empty)
            return
        y_min, y_max = min(values), max(values)
        padding = max((y_max - y_min) * 0.12, 1)
        y_min -= padding
        y_max += padding
        self.yrange = (y_min, y_max)

        def to_pixel(x, y):
            return QPointF(
                plot_area.left() + (x - x_min) / x_span * plot_area.width(),
                plot_area.bottom() - (y - y_min) / (y_max - y_min) * plot_area.height(),
            )

        for i in range(5):
            fraction = i / 4
            y = plot_area.bottom() - fraction * plot_area.height()
            value = y_min + (y_max - y_min) * fraction
            painter.setPen(QPen(QColor("#eaf0e7"), 1))
            painter.drawLine(
                QPointF(plot_area.left(), y), QPointF(plot_area.right(), y)
            )
            painter.setPen(QColor("#75877b"))
            painter.drawText(
                QRectF(0, y - 8, 50, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{value:.0f}" if abs(value) > 10 else f"{value:.1f}",
            )
            x = plot_area.left() + fraction * plot_area.width()
            xvalue = x_min + x_span * fraction
            painter.drawText(
                QRectF(x - 32, plot_area.bottom() + 8, 64, 20),
                Qt.AlignCenter,
                elapsed(xvalue) if self.time_axis else f"{xvalue:.0f}",
            )
        painter.save()
        painter.setClipRect(plot_area)
        for name, color, points in self.series:
            painter.setPen(QPen(QColor(color), 1.5))
            path = QPainterPath()
            previous_x = None
            # Preserve local extrema when the plot is narrower than its data.
            if len(points) > int(plot_area.width() * 4) and not self.scatter:
                bins = {}
                for x, y in points:
                    key = int((x - x_min) / x_span * plot_area.width())
                    if y is None:
                        bins.setdefault(key, []).append((x, y))
                        continue
                    bins.setdefault(key, []).append((x, y))
                compressed = []
                for group in bins.values():
                    valid = [v for v in group if v[1] is not None]
                    if valid:
                        compressed.extend(
                            sorted(
                                set(
                                    [
                                        valid[0],
                                        min(valid, key=lambda v: v[1]),
                                        max(valid, key=lambda v: v[1]),
                                        valid[-1],
                                    ]
                                )
                            )
                        )
                    if len(valid) != len(group):
                        compressed.append((group[-1][0], None))
                points = compressed
            for x, y in points:
                if y is None or not math.isfinite(y):
                    previous_x = None
                    continue
                if not x_min <= x <= x_max:
                    continue
                position = to_pixel(x, y)
                if self.scatter or name == "Excluded RR":
                    painter.setBrush(QColor(color))
                    painter.drawEllipse(position, 2.2, 2.2)
                elif previous_x is None or x - previous_x > self.gap or x < previous_x:
                    path.moveTo(position)
                else:
                    path.lineTo(position)
                if not self.scatter and name != "Excluded RR" and len(points) <= 20:
                    painter.setBrush(QColor(color))
                    painter.drawEllipse(position, 2.2, 2.2)
                previous_x = x
            if not self.scatter:
                painter.drawPath(path)
        if self.cursor is not None:
            x = max(plot_area.left(), min(self.cursor, plot_area.right()))
            painter.setPen(QPen(QColor("#81978a"), 1, Qt.DashLine))
            painter.drawLine(
                QPointF(x, plot_area.top()), QPointF(x, plot_area.bottom())
            )
        painter.restore()
        painter.setPen(QColor("#75877b"))
        painter.drawText(QRectF(0, 0, 55, 17), Qt.AlignRight, self.unit)

    def mouseMoveEvent(self, event):
        plot_area = self.plot_rect()
        self.cursor = event.position().x()
        self.update()
        if not (plot_area.left() <= self.cursor <= plot_area.right()):
            return
        x = self.xrange[0] + (self.cursor - plot_area.left()) / plot_area.width() * (
            self.xrange[1] - self.xrange[0]
        )
        lines = []
        for start, end, value in self.sound_intervals:
            if start <= x < end:
                lines.append(f"Sound: {value.title()} · {elapsed(start)} – {elapsed(end)}")
                break
        for name, _, points in self.series:
            if not points:
                continue
            # Plots retain source points; hovering reports the closest observation, not interpolation.
            point = min(points, key=lambda v: abs(v[0] - x))
            if point[1] is not None:
                lines.append(
                    f"{name}: {point[1]:.2f} {self.unit} at "
                    + (elapsed(point[0]) if self.time_axis else f"{point[0]:.1f} ms")
                )
        if lines:
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)

    def leaveEvent(self, event):
        self.cursor = None
        self.update()
        QToolTip.hideText()


class Dashboard(QWidget):
    """Display read-only session analysis across five tabs and a shared range."""

    back = Signal()

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.start = 0
        self.end = max(0.01, model.duration)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(12)
        heading = QHBoxLayout()
        back = QPushButton("Close analysis")
        back.clicked.connect(self.back.emit)
        heading.addWidget(back)
        section = text("SESSION ANALYSIS", "eyebrow")
        section.setMinimumWidth(175)
        heading.addWidget(section)
        heading.addStretch()
        m = model.manifest
        heading.addWidget(
            text(
                ("SIMULATED  ·  " if m.get("simulated") else "") + m["status"].upper(),
                "pill",
            )
        )
        root.addLayout(heading)
        root.addWidget(
            text(
                m["session_id"]
                + "  ·  "
                + elapsed(model.duration)
                + "  ·  Participant "
                + (m.get("participant_code") or "—"),
                "muted",
            )
        )
        controls = QHBoxLayout()
        controls.addWidget(text("Analyze range", "muted"))
        self.from_spin = QDoubleSpinBox()
        self.to_spin = QDoubleSpinBox()
        for spin in (self.from_spin, self.to_spin):
            spin.setDecimals(1)
            spin.setRange(0, self.end)
            spin.setSuffix(" s")
            spin.setSingleStep(5)
            spin.setFixedWidth(115)
        self.to_spin.setValue(self.end)
        controls.addWidget(self.from_spin)
        controls.addWidget(text("to"))
        controls.addWidget(self.to_spin)
        apply = QPushButton("Apply range")
        apply.clicked.connect(self.apply_range)
        controls.addWidget(apply)
        reset = QPushButton("Full session")
        reset.clicked.connect(self.reset_range)
        controls.addWidget(reset)
        controls.addStretch()
        self.range_note = text("Hover over a chart for values.", "muted")
        self.range_note.setMinimumWidth(200)
        controls.addWidget(self.range_note)
        root.addLayout(controls)
        root.addWidget(text(
            "Sound labels: " + model.sound_labels_status +
            (" · " + " / ".join(value.title() for value in SOUND_COLORS)
             + " · hover over a chart’s top strip for state and timing"
             if model.sound_labels else ""), "muted"))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.overview = self.page("Overview")
        self.cardiac = self.page("RR && variability")
        self.respiration = self.page("Respiration")
        self.quality = self.page("Motion && quality")
        self.ecg = self.page("ECG explorer")
        self._build_overview()
        self._build_cardiac()
        self._build_respiration()
        self._build_quality()
        self._build_ecg()
        for box in (
            self.overview,
            self.cardiac,
            self.respiration,
            self.quality,
            self.ecg,
        ):
            box.addStretch()
        self.render()

    def _build_overview(self):
        """Create the overview cards, trend charts, and observation notes."""
        self.overview_cards = self.metrics(
            self.overview,
            [
                ("MEAN HEART RATE", "bpm · reported samples"),
                ("RR VARIABILITY · RMSSD", "ms · exploratory"),
                ("RESPIRATION · DERIVED", "breaths/min · mean estimate"),
                ("ECG COVERAGE", "% of selected time"),
            ],
        )
        self.observations = text("")
        self.observations.setStyleSheet(
            "background:#e7eee3;padding:14px;border-radius:8px;color:#3f5e4c;"
        )
        # Insights follow the trend charts so the overview opens on data.
        self.hr_chart = self.plot(
            self.overview,
            "HEART RATE OVER TIME",
            "bpm",
            "Recorded heart rate. Gaps in the line are left unfilled.",
            GREEN,
        )
        self.resp_chart = self.plot(
            self.overview,
            "RESPIRATION ESTIMATES",
            "br/min",
            "Derived from overlapping 60-second ECG windows, positioned at their end times.",
            BLUE,
        )
        self.overview.addWidget(self.observations)

    def _build_cardiac(self):
        """Create RR interval, variability, and successive-interval charts."""
        self.cardiac_cards = self.metrics(
            self.cardiac,
            [
                ("RMSSD", "ms · adjacent RR changes"),
                ("SDRR", "ms · spread of retained RR"),
                ("pRR50", "% of adjacent changes >50 ms"),
                ("MEAN RR", "ms · retained intervals"),
            ],
        )
        self.rr_note = text("", "muted")
        self.cardiac.addWidget(self.rr_note)
        self.rr_chart = self.plot(
            self.cardiac,
            "RR INTERVALS",
            "ms",
            "Original RR intervals. Amber points are outside the 300–2,000 ms analysis bounds.",
            GREEN,
        )
        self.rmssd_chart = self.plot(
            self.cardiac,
            "ROLLING RMSSD · 60-SECOND WINDOWS",
            "ms",
            "Every 10 seconds, with at least 30 retained intervals and 20 usable adjacent pairs.",
            BLUE,
        )
        f, b = card("SUCCESSIVE INTERVALS · POINCARÉ VIEW")
        self.poincare = Chart("ms", False, True)
        b.addWidget(self.poincare)
        b.addWidget(
            text(
                "Horizontal: RR(n) · Vertical: RR(n+1). Each point is an eligible adjacent pair.",
                "muted",
            )
        )
        self.cardiac.addWidget(f)

    def _build_respiration(self):
        """Create saved respiration metrics and confidence charts."""
        self.resp_cards = self.metrics(
            self.respiration,
            [
                ("MEAN ESTIMATE", "breaths/min"),
                ("ESTIMATE RANGE", "breaths/min · min–max"),
                ("ACCEPTED WINDOWS", "usable / saved in range"),
                ("MEAN CONCENTRATION", "spectral concentration · 0–1"),
            ],
        )
        self.resp_detail = self.plot(
            self.respiration,
            "RESPIRATION RATE · DERIVED",
            "br/min",
            "No direct respiratory sensor is present. Estimates are exploratory.",
            BLUE,
        )
        self.conf_chart = self.plot(
            self.respiration,
            "SPECTRAL CONCENTRATION",
            "0–1",
            "Algorithm score, not a probability of accuracy. Higher values mean a more concentrated spectral peak.",
            AMBER,
        )
        self.resp_text = text("", "muted")
        self.respiration.addWidget(self.resp_text)

    def _build_quality(self):
        """Create motion summaries and the recording-event table."""
        self.motion_cards = self.metrics(
            self.quality,
            [
                ("MOTION PROXY · RMS", "mg · gravity-magnitude deviation"),
                ("HIGH-MOTION SAMPLES", "% above 450 mg deviation"),
                ("ACC COVERAGE", "% of selected time"),
                ("DROPOUT STARTS", "events within selected range"),
            ],
        )
        self.motion_chart = self.plot(
            self.quality,
            "MOTION OVER TIME",
            "mg",
            "One-second RMS of |acceleration magnitude − 1,000 mg|. Bins are selected by their midpoint; this is a movement proxy.",
            AMBER,
        )
        self.axes_chart = self.plot(
            self.quality,
            "ACCELEROMETER AXES",
            "mg",
            "One-second means: X green · Y blue · Z amber. Gravity and orientation are retained.",
            GREEN,
        )
        self.quality_text = text("", "muted")
        self.quality.addWidget(self.quality_text)
        self.event_table = QTableWidget(0, 3)
        self.event_table.setHorizontalHeaderLabels(["Time", "Event", "Details"])
        self.event_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.event_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.event_table.setMinimumHeight(210)
        self.quality.addWidget(self.event_table)

    def _build_ecg(self):
        """Create the raw ECG browser and amplitude overview."""
        self.ecg.addWidget(
            text(
                "Browse the original saved ECG. The slider moves through the selected range; raw sample amplitudes are unchanged.",
                "muted",
            )
        )
        controls = QHBoxLayout()
        self.ecg_label = text("")
        controls.addWidget(self.ecg_label)
        controls.addStretch()
        controls.addWidget(text("Window", "muted"))
        self.ecg_width = QComboBox()
        self.ecg_width.addItems(["5 seconds", "10 seconds", "20 seconds"])
        self.ecg_width.setCurrentIndex(1)
        self.ecg_width.currentIndexChanged.connect(self.update_ecg)
        controls.addWidget(self.ecg_width)
        self.ecg.addLayout(controls)
        self.ecg_slider = QSlider(Qt.Horizontal)
        self.ecg_slider.valueChanged.connect(self.update_ecg)
        self.ecg.addWidget(self.ecg_slider)
        self.ecg_chart = self.plot(
            self.ecg,
            "RAW ECG · DETAIL",
            "µV",
            "Gaps remain visible. Auto-scaled amplitude; no diagnostic interpretation.",
            GREEN,
        )
        self.ecg_chart.setMinimumHeight(320)
        self.envelope_chart = self.plot(
            self.ecg,
            "ECG AMPLITUDE THROUGHOUT THE RANGE",
            "µV",
            "One-second minimum (blue) and maximum (green). Use the slider above to inspect individual beats.",
            GREEN,
        )

    def page(self, title):
        """Create a scrollable tab and return its content layout."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 12, 8, 12)
        box.setSpacing(13)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, title)
        return box

    def metrics(self, box, definitions):
        """Add a row of metric cards and return their value labels."""
        row = QHBoxLayout()
        row.setSpacing(10)
        values = []
        for title, caption in definitions:
            frame, card_layout = card(title)
            value = text("—", "metric")
            value.setStyleSheet("font-size:27px;")
            card_layout.addWidget(value)
            card_layout.addWidget(text(caption, "muted"))
            row.addWidget(frame, 1)
            values.append(value)
        box.addLayout(row)
        return values

    def plot(self, box, title, unit, caption, color):
        """Add a titled chart card with its explanatory caption."""
        frame, card_layout = card(title)
        chart = Chart(unit)
        chart.sound_intervals = self.model.sound_intervals()
        card_layout.addWidget(chart)
        card_layout.addWidget(text(caption, "muted"))
        box.addWidget(frame)
        return chart

    def apply_range(self):
        if self.to_spin.value() <= self.from_spin.value():
            self.range_note.setText("End must be after start.")
            return
        self.start = self.from_spin.value()
        self.end = self.to_spin.value()
        self.render()

    def reset_range(self):
        self.from_spin.setValue(0)
        self.to_spin.setValue(self.model.duration)
        self.start = 0
        self.end = max(0.01, self.model.duration)
        self.render()

    def render(self):
        """Refresh cards, plots, and events for the current analysis range."""
        summary = self.model.summary(self.start, self.end)
        self.summary = summary
        span = (self.start, self.end)
        self.range_note.setText(
            elapsed(self.start) + " – " + elapsed(self.end) + " · hover for values"
        )
        self._render_overview(summary, span)
        self._render_cardiac(summary, span)
        self._render_respiration(summary, span)
        self._render_quality(summary, span)
        self._render_ecg(span)

    def _render_overview(self, summary, span):
        """Update overall metrics and heart-rate/respiration trends."""
        metric_values = [
            number(summary["hr"]["mean"]),
            number(summary["rr"]["rmssd"]),
            number(summary["respiration"]["mean"]),
            number(summary["ecg_coverage"]),
        ]
        for widget, value in zip(self.overview_cards, metric_values):
            widget.setText(value)
        self.observations.setText("\n\n".join(summary["notes"]))
        self.hr_chart.set_data(
            [("Heart rate", GREEN, [(r["t"], r["value"]) for r in summary["hr_rows"]])],
            span,
        )
        resp_points = [(r["t"], r["value"]) for r in summary["resp_rows"]]
        for chart in (self.resp_chart, self.resp_detail):
            chart.set_data([("ECG-derived rate", BLUE, resp_points)], span, 12)

    def _render_cardiac(self, summary, span):
        """Update original RR observations and adjacent-pair variability."""
        rr = summary["rr"]
        metric_values = [
            number(rr["rmssd"]),
            number(rr["sdrr"]),
            number(rr["prr50"]),
            number(rr["mean"]),
        ]
        for widget, value in zip(self.cardiac_cards, metric_values):
            widget.setText(value)
        self.rr_note.setText(
            f'{rr["count"]} retained intervals · {rr["excluded"]} excluded · {len(rr["pairs"])} adjacent pairs. Values use a simple range filter only; normal beats are not identified, so SDRR and pRR50 are not clinical SDNN/pNN50. No interpolation or ectopic correction. '
            + (
                "Too few observations for variability estimates."
                if not rr["sufficient"]
                else ""
            )
        )
        all_rr = [r for r in self.model.rr if self.start <= r["t"] <= self.end]
        self.rr_chart.set_data(
            [
                (
                    "RR",
                    GREEN,
                    [
                        (r["t"], r["ms"] if 300 <= r["ms"] <= 2000 else None)
                        for r in all_rr
                    ],
                ),
                (
                    "Excluded RR",
                    AMBER,
                    [(r["t"], r["ms"]) for r in all_rr if not 300 <= r["ms"] <= 2000],
                ),
            ],
            span,
        )
        rolling = []
        for end in np.arange(self.start + 60, self.end + 0.001, 10):
            rolling.append(
                (
                    float(end),
                    rr_metrics(self.model.rr, float(end) - 60, float(end))["rmssd"],
                )
            )
        self.rmssd_chart.set_data([("Rolling RMSSD", BLUE, rolling)], span, 12)
        pairs = rr["pairs"]
        xs = [p[0] for p in pairs]
        self.poincare.set_data(
            [("Successive RR", GREEN, pairs)],
            (min(xs) - 20, max(xs) + 20) if xs else (0, 1),
        )

    def _render_respiration(self, summary, span):
        """Update accepted estimates and spectral concentration."""
        good = [r for r in summary["resp_rows"] if r["value"] is not None]
        confidence = [
            r["confidence"]
            for r in good
            if isinstance(r["confidence"], (int, float))
            and math.isfinite(r["confidence"])
        ]
        metric_values = [
            number(summary["respiration"]["mean"]),
            (
                f'{number(summary["respiration"]["min"])}–{number(summary["respiration"]["max"])}'
                if good
                else "—"
            ),
            f'{len(good)} / {len(summary["resp_rows"])}',
            number(float(np.mean(confidence)) if confidence else None, 2),
        ]
        for widget, value in zip(self.resp_cards, metric_values):
            widget.setText(value)
        self.conf_chart.set_data(
            [
                (
                    "Spectral concentration",
                    AMBER,
                    [
                        (r["t"], r["confidence"])
                        for r in summary["resp_rows"]
                        if isinstance(r["confidence"], (int, float))
                    ],
                )
            ],
            span,
            12,
        )
        self.resp_text.setText(
            f"Saved algorithm: {self.model.resp_algorithm}. This rate is inferred from ECG amplitude changes and can reflect artifact or motion. Window overlap means adjacent estimates are not independent. Compare with a respiratory reference before scientific interpretation."
        )

    def _render_quality(self, summary, span):
        """Update motion charts, integrity notes, and recording events."""
        metric_values = [
            number(summary["motion_rms"]),
            number(summary["motion_high_pct"]),
            number(summary["acc_coverage"]),
            str(summary["dropouts"]),
        ]
        for widget, value in zip(self.motion_cards, metric_values):
            widget.setText(value)
        motion_bins = sorted(
            (t, b)
            for t, b in self.model.motion.items()
            if self.start <= t + 0.5 <= self.end
        )
        self.motion_chart.set_data(
            [
                (
                    "Motion proxy",
                    AMBER,
                    [
                        (t + 0.5, math.sqrt(b["sumsq"] / b["count"]))
                        for t, b in motion_bins
                    ],
                )
            ],
            span,
            2,
        )
        self.axes_chart.set_data(
            [
                (
                    k.upper(),
                    color,
                    [(t + 0.5, b[k] / b["count"]) for t, b in motion_bins],
                )
                for k, color in zip(("x", "y", "z"), (GREEN, BLUE, AMBER))
            ],
            span,
            2,
        )
        events = [e for e in self.model.events if self.start <= e["t"] <= self.end]
        active_flags = [
            e for e in events if e["category"] == "quality" and e.get("active", True)
        ]
        issues = "\n".join(
            str(e.get("file", e.get("signal", "Session"))) + ": " + e["error"]
            for e in self.model.issues
        )
        self.quality_text.setText(
            f'{self.model.integrity["valid_chunks"]} valid raw chunks in the full session · {len(self.model.issues)} analysis/integrity issues. {len(active_flags)} quality warnings recorded in this range. An absence of flags does not certify signal quality. Coverage is timestamp availability, not physiological quality.'
            + ("\n\n" + issues if issues else "")
        )
        self.event_table.setRowCount(len(events))
        for i, event in enumerate(events):
            detail = ", ".join(
                f"{k}: {value}"
                for k, value in event.items()
                if k
                not in (
                    "t",
                    "utc",
                    "type",
                    "category",
                    "device_id",
                    "sensor_ns",
                    "host_monotonic_s",
                )
            )
            for j, value in enumerate(
                (
                    elapsed(event["t"]),
                    event["type"].replace("_", " "),
                    detail or event["category"],
                )
            ):
                self.event_table.setItem(i, j, QTableWidgetItem(value))
        self.event_table.resizeColumnToContents(0)
        self.event_table.resizeColumnToContents(1)

    def _render_ecg(self, span):
        """Reset the ECG browser range and update its amplitude envelope."""
        self.ecg_slider.blockSignals(True)
        self.ecg_slider.setRange(
            round(self.start * 10), round(max(self.start, self.end - 1) * 10)
        )
        self.ecg_slider.setValue(round(self.start * 10))
        self.ecg_slider.blockSignals(False)
        ecg_bins = sorted(
            (t, b)
            for t, b in self.model.ecg_bins.items()
            if self.start <= t + 0.5 <= self.end
        )
        self.envelope_chart.set_data(
            [
                ("Maximum", GREEN, [(t + 0.5, b["max"]) for t, b in ecg_bins]),
                ("Minimum", BLUE, [(t + 0.5, b["min"]) for t, b in ecg_bins]),
            ],
            span,
            2,
        )
        self.update_ecg()

    def update_ecg(self):
        """Load only the raw ECG window selected by the slider and width control."""
        if not hasattr(self, "ecg_slider"):
            return
        start = self.ecg_slider.value() / 10
        end = min(self.end, start + [5, 10, 20][self.ecg_width.currentIndex()])
        self.ecg_label.setText(elapsed(start) + " – " + elapsed(end))
        try:
            points = self.model.ecg_window(start, end)
        except (OSError, ValueError, KeyError):
            points = []
            self.ecg_label.setText(
                "ECG could not be read; return to the library and verify this session."
            )
        self.ecg_chart.set_data(
            [("Raw ECG", GREEN, points)], (start, max(start + 0.01, end)), 0.05
        )
