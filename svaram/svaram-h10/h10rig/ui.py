"""Qt recording controls and session library. Acquisition runs in engine.py."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import Qt, QTimer, QUrl, QLockFile, QSettings
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QFrame,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QMessageBox,
    QFileDialog,
    QPlainTextEdit,
    QStackedWidget,
    QScrollArea,
    QSizePolicy,
)

from .engine import Engine
from .storage import verify_session, recover_session, export_session, iter_rows
from .respiration import recalculate
from .analysis import SessionAnalysis
from .dashboard import Dashboard
from .labels import SOUND_ELEMENTS
from .registry import Registry

STYLE = """
QMainWindow,QWidget { background:#f4f5f0; color:#233d36; font-family:"Helvetica Neue"; font-size:14px; }
QLabel#eyebrow { color:#72827a; font-size:11px; font-weight:600; letter-spacing:2px; }
QLabel#title { font-size:29px; font-weight:600; }
QLabel#muted { color:#6b7d74; font-size:12px; }
QLabel#metric { font-size:34px; font-weight:500; }
QLabel#pill { background:#e1ebe2; color:#326c4f; padding:8px 14px; border-radius:13px; font-size:12px; font-weight:600; }
QFrame#card { background:#ffffff; border:1px solid #dce3d9; border-radius:14px; }
QFrame#card QLabel { background:transparent; }
QPushButton { background:#e8ede5; color:#233d36; border:1px solid #d5dfd1; border-radius:7px; padding:10px 16px; font-weight:500; }
QPushButton:hover { background:#dbe5d9; color:#233d36; }
QPushButton:pressed { background:#cbdac8; color:#233d36; }
QPushButton:checked { background:#295b46; color:white; }
QPushButton:checked:hover,QPushButton:checked:pressed { background:#214a39; color:white; }
QPushButton:disabled { color:#637168; background:#edf0ea; border-color:#d5dfd1; }
QPushButton:checked:disabled { color:#334b3e; background:#d5e1d8; }
QPushButton#primary { background:#295b46; color:white; border:0; }
QPushButton#primary:hover { background:#214a39; }
QPushButton#primary:disabled { background:#d5dfd8; color:#526458; }
QPushButton#stop { color:#954d40; background:#f4e9e3; border-color:#e4c9be; }
QPushButton#stop:disabled { color:#78665f; background:#eee9e5; border-color:#ded3cc; }
QLineEdit,QComboBox { background:white; border:1px solid #d4ddd0; border-radius:6px; padding:9px; }
QTabWidget::pane { border:0; }
QTabBar::tab { padding:12px 20px; color:#75847a; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#295b46; border-bottom:2px solid #295b46; }
QTableWidget { background:white; alternate-background-color:#f4f7f1; border:1px solid #dce3d9; gridline-color:#e9eee5; }
QHeaderView::section { background:#e9eee5; color:#587062; padding:9px; border:0; }
QPlainTextEdit { background:white; border:1px solid #dce3d9; border-radius:8px; padding:10px; font-family:Menlo; font-size:12px; }
"""


def label(text, style=None):
    widget = QLabel(text)
    if style:
        widget.setObjectName(style)
    return widget


def button(text, fn, style=None):
    widget = QPushButton(text)
    widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
    widget.setMinimumHeight(42)
    widget.clicked.connect(fn)
    if style:
        widget.setObjectName(style)
    return widget


def card():
    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(20, 18, 20, 18)
    box.setSpacing(10)
    return frame, box


class Trace(QWidget):
    """Paint the last eight seconds of live ECG without drawing across gaps."""

    def __init__(self):
        super().__init__()
        self.points = []
        self.setMinimumHeight(80)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#fcfdf9"))
        painter.setPen(QPen(QColor("#eaf0e6"), 1))
        for x in range(0, width, 25):
            painter.drawLine(x, 0, x, height)
        for y in range(0, height, 25):
            painter.drawLine(0, y, width, y)
        if len(self.points) < 2:
            painter.setPen(QColor("#86968a"))
            painter.drawText(
                self.rect(), Qt.AlignCenter, "Live ECG appears after connecting"
            )
            return
        end = self.points[-1][0]
        start = end - 8
        selected = [v for v in self.points if v[0] >= start]
        scale = max(500, min(10000, max(abs(v[1]) for v in selected) * 1.15))
        path = QPainterPath()
        previous_time = None
        for t, value in selected:
            x = (t - start) / 8 * width
            y = height * 0.62 - value / scale * height * 0.48
            if previous_time is None or t - previous_time > 0.05:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
            previous_time = t
        painter.setPen(QPen(QColor("#38795a"), 1.6))
        painter.drawPath(path)
        painter.setPen(QColor("#829482"))
        painter.drawText(8, 17, f"±{scale/1000:.1f} mV  ·  8 seconds  ·  auto scale")


class Window(QMainWindow):
    """Coordinate recording controls, background jobs, and the session library."""

    def __init__(self, root):
        super().__init__()
        self.root = Path(root)
        self.engine = Engine(self.root / "sessions")
        self.registry = None
        self.data_lock = None
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="Session tools"
        )
        self.jobs = []
        self.busy = False
        self.sound_future = None
        self.requested_sound = "silence"
        self.devices = []
        self.current_path = None
        self.analysis_windows = {}
        self.closing = False
        self.shutdown_done = False
        self.setWindowTitle("Svaram · Polar H10 Measurement Rig")
        self.resize(1140, 880)
        self.setMinimumSize(940, 740)
        outer = QWidget()
        self.setCentralWidget(outer)
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(30, 22, 30, 20)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        titlebox = QVBoxLayout()
        titlebox.addWidget(label("SVARAM   /   MEASUREMENT RIG", "eyebrow"))
        titlebox.addWidget(label("A clear view of every beat.", "title"))
        heading.addLayout(titlebox)
        heading.addStretch()
        self.pill = label("OFFLINE  ·  LOCAL STORAGE", "pill")
        heading.addWidget(self.pill)
        layout.addLayout(heading)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        record = QWidget()
        record_scroll = QScrollArea()
        record_scroll.setWidgetResizable(True)
        record_scroll.setFrameShape(QFrame.NoFrame)
        record_scroll.setWidget(record)
        self.tabs.addTab(record_scroll, "Record")
        body = QVBoxLayout(record)
        body.setContentsMargins(0, 16, 0, 0)
        body.setSpacing(14)

        # Sensor selection and connection controls.
        device, box = card()
        body.addWidget(device)
        top = QHBoxLayout()
        top.addWidget(label("01   SENSOR", "eyebrow"))
        top.addStretch()
        self.connection = label("Disconnected", "muted")
        top.addWidget(self.connection)
        box.addLayout(top)
        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setMinimumWidth(220)
        self.combo.addItem("Select a Polar H10…")
        row.addWidget(self.combo, 1)
        self.scan_button = button("Scan for H10", self.scan)
        row.addWidget(self.scan_button)
        self.connect_button = button("Connect", self.connect, "primary")
        row.addWidget(self.connect_button)
        self.demo_button = button("Use simulator", self.demo)
        row.addWidget(self.demo_button)
        box.addLayout(row)
        self.sensor_note = label(
            "Wear the moistened strap. Connect, then inspect the trace before recording.",
            "muted",
        )
        box.addWidget(self.sensor_note)

        # Live values are refreshed from an engine snapshot by the display timer.
        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        self.hr = self.metric_card(metrics, "HEART RATE", "—", "bpm · from sensor")
        self.resp = self.metric_card(
            metrics, "RESPIRATION · DERIVED", "—", "breaths/min · ECG estimate"
        )
        self.duration = self.metric_card(
            metrics, "SESSION TIME", "00:00", "monotonic recording clock"
        )
        body.addLayout(metrics)

        # Trace preview and signal quality.
        signal, box = card()
        body.addWidget(signal, 1)
        line = QHBoxLayout()
        line.addWidget(label("03   LIVE ECG & SIGNAL CHECK", "eyebrow"))
        line.addStretch()
        line.addWidget(label("ECG 130 Hz   /   MOTION 50 Hz", "muted"))
        box.addLayout(line)
        self.trace = Trace()
        box.addWidget(self.trace, 1)
        self.quality = label("Connect a sensor to check signal", "muted")
        self.quality.setWordWrap(True)
        box.addWidget(self.quality)
        self.resp_note = label(
            "Respiration uses 60 seconds of saved ECG. It is not a directly measured respiratory signal.",
            "muted",
        )
        self.resp_note.setWordWrap(True)
        box.addWidget(self.resp_note)

        # Participant entry and recording actions.
        session, box = card()
        body.insertWidget(1, session)
        box.addWidget(label("02   RECORD SESSION & SOUND LABELS", "eyebrow"))
        row = QHBoxLayout()
        self.participant = QComboBox()
        self.participant.setMinimumWidth(280)
        box.addWidget(self.participant)
        self.start_button = button("Start recording", self.start, "primary")
        row.addWidget(self.start_button)
        self.stop_button = button("Stop && save", self.stop, "stop")
        row.addWidget(self.stop_button)
        self.drop_button = button("Test 20s dropout", self.dropout)
        row.addWidget(self.drop_button)
        box.addLayout(row)
        setup = QHBoxLayout()
        self.root_button = button("Study folder…", self.choose_study_root)
        self.registry_button = button("Refresh visits", self.refresh_registry)
        setup.addWidget(self.root_button)
        setup.addWidget(self.registry_button)
        self.phase_buttons = []
        for phase, title in (("rest", "Mark rest"), ("sound", "Mark sound"), ("recovery", "Mark recovery")):
            control = button(title, lambda checked=False, p=phase: self.action(
                self.engine.mark_phase(p), lambda value: self.message.setText("Phase saved: " + value)))
            setup.addWidget(control)
            self.phase_buttons.append(control)
        box.addLayout(setup)
        self.sound_controls = QWidget()
        sound_row = QHBoxLayout(self.sound_controls)
        sound_row.setContentsMargins(0, 0, 0, 0)
        self.sound_buttons = {}
        for value in SOUND_ELEMENTS:
            control = button(value.title(), lambda checked=False, v=value: self.choose_sound(v))
            control.setCheckable(True)
            control.setToolTip("Click again for silence")
            sound_row.addWidget(control)
            self.sound_buttons[value] = control
        self.sound_controls.hide()
        box.addWidget(self.sound_controls)
        self.sound_state = label("Sound: Silence", "muted")
        box.addWidget(self.sound_state)
        self.registry_note = label("", "muted")
        self.registry_note.setWordWrap(True)
        box.addWidget(self.registry_note)
        self.refresh_registry()
        self.write = label(
            "No active session · raw data saves automatically every 5 seconds", "muted"
        )
        self.write.setWordWrap(True)
        box.addWidget(self.write)

        # The library and session dashboard share this navigation stack.
        self.library_stack = QStackedWidget()
        library = QWidget()
        self.library_stack.addWidget(library)
        self.tabs.addTab(self.library_stack, "Saved sessions")
        lib = QVBoxLayout(library)
        lib.setContentsMargins(0, 16, 0, 0)
        actions = QHBoxLayout()
        actions.addWidget(label("SESSION LIBRARY", "eyebrow"))
        actions.addStretch()
        self.analysis_button = button("Open analysis", self.open_analysis, "primary")
        actions.addWidget(self.analysis_button)
        actions.addWidget(button("Refresh", self.refresh_sessions))
        actions.addWidget(button("Open session folder…", self.open_session))
        actions.addWidget(
            button(
                "Show storage",
                lambda: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(self.engine.root))
                ),
            )
        )
        lib.addLayout(actions)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Session", "Participant", "Status", "Duration", "Source"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents
            )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self.select_session)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_analysis())
        lib.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText(
            "Select a session to inspect its manifest, integrity and recording events."
        )
        lib.addWidget(self.details, 1)
        actions = QHBoxLayout()
        self.verify_button = button("Verify && open", self.inspect)
        actions.addWidget(self.verify_button)
        self.recover_button = button("Recover incomplete", self.recover)
        actions.addWidget(self.recover_button)
        self.derive_button = button("Recalculate respiration", self.derive)
        actions.addWidget(self.derive_button)
        actions.addStretch()
        self.export_button = button("Export ZIP + CSV", self.export, "primary")
        actions.addWidget(self.export_button)
        lib.addLayout(actions)
        self.message = label("Ready. All measurements stay on this Mac.", "muted")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        # Poll futures here so their completion callbacks update widgets on the Qt thread.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.refresh_sessions()
        incomplete = [
            p
            for p in self.engine.root.iterdir()
            if p.is_dir() and (p / "active.json").exists()
        ]
        if incomplete:
            self.message.setText(
                f"{len(incomplete)} incomplete session(s) found. Open Saved sessions to verify and recover them."
            )
            self.tabs.setCurrentIndex(1)

    def metric_card(self, layout, title, value, caption):
        f, b = card()
        b.addWidget(label(title, "eyebrow"))
        l = label(value, "metric")
        b.addWidget(l)
        b.addWidget(label(caption, "muted"))
        layout.addWidget(f, 1)
        return l

    def track(self, future, callback=None, message="Working…", control=False):
        """Queue a future for the Qt timer to collect on the UI thread."""
        if control:
            self.busy = True
            # Disable immediately, not at the next 100-ms poll: double clicks
            # must not enqueue duplicate recording/transport operations.
            for widget in (self.start_button, self.stop_button, self.scan_button,
                           self.connect_button, self.demo_button, self.drop_button,
                           *self.phase_buttons, *self.sound_buttons.values()):
                widget.setEnabled(False)
        self.message.setText(message)
        self.jobs.append((future, callback, control))

    def action(self, coro, callback=None, message="Working…"):
        """Submit an acquisition coroutine and mark sensor controls busy."""
        self.track(self.engine.submit(coro), callback, message, True)

    def background(self, fn, callback=None, message="Working…"):
        """Run file or analysis work in the pool to keep the UI responsive."""
        self.track(self.pool.submit(fn), callback, message)

    def scan(self):
        self.action(
            self.engine.scan(), self.scanned, "Scanning nearby Bluetooth devices…"
        )

    def scanned(self, devices):
        self.devices = devices
        self.combo.clear()
        for d, name in devices:
            self.combo.addItem(f"{name}  ·  {d.address}", d)
        if not devices:
            self.combo.addItem(
                "No H10 found — check the strap and Bluetooth permission"
            )
        self.message.setText(f"Found {len(devices)} Polar H10 sensor(s).")

    def connect(self):
        if self.engine.want_connection:
            self.action(self.engine.stop(), message="Disconnecting…")
            return
        device = self.combo.currentData()
        if device is None:
            self.message.setText("Scan and select a Polar H10 first.")
            return
        self.action(
            self.engine.connect(device),
            message="Connecting and starting signal preview…",
        )

    def demo(self):
        self.action(
            self.engine.connect(simulated=True),
            message="Simulator connected. These are synthetic signals, not human measurements.",
        )

    def start(self):
        sid = self.participant.currentData()
        if sid is None and not self.engine.snapshot()["simulated"]:
            self.message.setText("Select a ready study visit first.")
            return
        self.action(
            self.engine.start(study_session_id=sid, registry=self.registry if sid else None),
            lambda p: self.message.setText("Recording to " + p),
            "Creating session storage…",
        )

    def choose_study_root(self):
        if self.engine.session or self.busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select the survey data folder", str(self.root))
        if not folder or Path(folder).resolve() == self.root.resolve():
            return
        try:
            registry = Registry(folder)
            lock = QLockFile(str(Path(folder) / ".svaram.lock"))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                raise ValueError("Another H10 app is using this folder")
            (Path(folder) / "sessions").mkdir(exist_ok=True)
            self.root = Path(folder)
            self.engine.root = self.root / "sessions"
            self.registry = registry
            self.data_lock = lock
            QSettings("Shobotics", "Svaram H10").setValue("study_root", folder)
            self.refresh_registry()
            self.refresh_sessions()
        except Exception as exc:
            QMessageBox.warning(self, "Study folder unavailable", str(exc))

    def refresh_registry(self):
        if self.engine.session:
            return
        selected = self.participant.currentData()
        self.participant.clear()
        self.participant.addItem("Select a study visit / unlinked simulator", None)
        try:
            self.registry = Registry(self.root)
            errors = self.registry.reconcile()
            for session in self.registry.sessions():
                if session["status"] in ("ready", "in_progress"):
                    text = (f'{session["code"]} · visit {session["visit"]:02d} · pre submitted · '
                            f'{session["recording_status"] or "ready"}')
                    self.participant.addItem(text, session["id"])
            index = self.participant.findData(selected)
            self.participant.setCurrentIndex(max(index, 0))
            self.registry_note.setText(str(self.root) + (" · Registry issues: " + "; ".join(errors) if errors else ""))
        except Exception as exc:
            self.registry = None
            self.registry_note.setText(f"Study registry unavailable: {exc} Select its folder or start the survey app.")

    def stop(self):
        self.action(
            self.engine.stop(),
            self.stopped,
            "Stopping streams and saving the final chunks…",
        )

    def stopped(self, path):
        """Refresh the library and recalculate respiration after a clean stop."""
        self.refresh_sessions()
        if not path:
            return
        self.current_path = Path(path)
        if (self.current_path / "active.json").exists():
            self.message.setText("Session is incomplete. Recover it in Saved sessions.")
            return
        self.background(
            lambda: recalculate(path),
            lambda _: self.message.setText(
                "Session saved. Respiration recalculated from raw ECG; ready to review or export."
            ),
            "Session saved. Calculating derived respiration…",
        )

    def dropout(self):
        self.action(
            self.engine.simulate_dropout(),
            message="Simulating a 20-second dropout. Recording remains open.",
        )

    def refresh_sessions(self):
        """Rebuild the library table from on-disk session manifests."""
        selected = self.current_path
        sessions = []
        paths = set(self.engine.root.glob("*/manifest.json"))
        paths.update((self.root / "recordings").glob("*/*/manifest.json"))
        paths.update((app_root() / "sessions").glob("*/manifest.json"))
        for manifest_path in sorted(paths, reverse=True):
            p = manifest_path.parent
            try:
                m = json.loads((p / "manifest.json").read_text())
                sessions.append((p, m))
            except (OSError, ValueError):
                continue
        if not self.engine.session:
            self.refresh_registry()
        self.table.setRowCount(len(sessions))
        for row, (p, m) in enumerate(sessions):
            status = m["status"]
            if (p / "active.json").exists() and not (
                self.engine.session and self.engine.session.path == p
            ):
                status = "incomplete"
            duration = int(m.get("duration_s", 0))
            values = [
                p.name,
                m.get("participant_code", "") or "—",
                status,
                f"{duration//60:02d}:{duration%60:02d}",
                "SIMULATED" if m.get("simulated") else "Polar H10",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, str(p))
                self.table.setItem(row, col, item)
            if p == selected:
                self.table.selectRow(row)

    def select_session(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        self.current_path = Path(item.data(Qt.UserRole))
        try:
            m = json.loads((self.current_path / "manifest.json").read_text())
            counts = "\n".join(
                f'  {k}: {v.get("samples",0):,} samples / {v.get("chunks",0)} chunks'
                for k, v in m["streams"].items()
            )
            self.details.setPlainText(
                f'{m["session_id"]}\nStatus: {m["status"]}\nStarted: {m["started_at"]}\nDevice: {m["device_id"]}\n\n{counts}\n\nSelect “Open analysis” for trends, variability, respiration, motion and ECG. “Verify & open” shows integrity details.'
            )
        except Exception as exc:
            self.details.setPlainText(str(exc))

    def open_analysis(self):
        """Load the selected session off-thread, then show its dashboard."""
        if not self.current_path or self.jobs or self.closing:
            return
        path = self.current_path.resolve()
        if path in self.analysis_windows:
            window = self.analysis_windows[path]
            window.showNormal()
            window.raise_()
            window.activateWindow()
            return

        def done(model):
            if self.closing:
                return
            window = QMainWindow(self, Qt.Window)
            window.setAttribute(Qt.WA_DeleteOnClose)
            window.setWindowTitle("Svaram · Analysis · " + model.manifest["session_id"])
            window.resize(1140, 880)
            dashboard = Dashboard(model)
            dashboard.back.connect(window.close)
            window.setCentralWidget(dashboard)
            self.analysis_windows[path] = window
            window.destroyed.connect(lambda *_: self.analysis_windows.pop(path, None))
            window.show()
            self.message.setText(
                "Analysis of "
                + path.name
                + " · calculated locally from saved observations."
            )

        self.background(
            lambda: SessionAnalysis(path),
            done,
            "Reading and verifying the session for analysis…",
        )

    def choose_sound(self, value):
        """Queue every click in order without blocking recording controls."""
        state = self.engine.snapshot()
        if not state["recording"] or self.busy or self.closing:
            return
        pressed_at = time.monotonic()
        pending = self.sound_future is not None
        current = self.requested_sound if pending else state["sound_label"]
        target = "silence" if value == current else value
        self.requested_sound = target
        self.sound_future = self.engine.submit(self.engine.set_sound_label(target, pressed_at))
        self.track(self.sound_future,
                   lambda saved: self.message.setText("Sound label saved: " + saved.title()),
                   message="Saving sound label…")
        for name, control in self.sound_buttons.items():
            control.setChecked(name == target)
        self.sound_state.setText("Sound: " + state["sound_label"].title()
                                + " · saving " + target.title() + "…")

    def open_session(self):
        path = QFileDialog.getExistingDirectory(
            self, "Open a session folder", str(self.engine.root)
        )
        if path:
            self.current_path = Path(path)
            self.open_analysis()

    def inspect(self):
        """Verify the session and show its metadata, events, and initial ECG."""
        if not self.current_path:
            return
        path = self.current_path

        def work():
            report = verify_session(path)
            m = json.loads((path / "manifest.json").read_text())
            events = []
            for f in sorted((path / "events").glob("*.jsonl")):
                from collections import deque

                with f.open() as source:
                    lines = list(deque(source, maxlen=40))
                events.append(f.name + " (last 40)\n" + "".join(lines))
            samples = []
            for r in iter_rows(path, "ecg"):
                samples.append((r["t"], r["uv"]))
                if len(samples) >= 1040:
                    break
            return report, m, events, samples

        def done(data):
            report, m, events, samples = data
            self.details.setPlainText(
                f'{m["session_id"]} · {m["status"]}\n{report["valid_chunks"]} valid chunks · {len(report["errors"])} integrity issues\n\n'
                + json.dumps(report["errors"], indent=2)
                + "\n\nMANIFEST\n"
                + json.dumps({k: v for k, v in m.items() if k != "files"}, indent=2)
                + "\n\n"
                + "\n".join(events)
            )
            self.message.setText(
                f'Verified {report["valid_chunks"]} valid chunks. {len(report["errors"])} issue(s); all source files preserved.'
            )
            if samples:
                dialog = QMessageBox(self)
                dialog.setWindowTitle("Session verified")
                dialog.setText(
                    f'{report["valid_chunks"]} valid chunks; {len(report["errors"])} issue(s).\nDetails and events are shown in the session panel.'
                )
                # Show a native, persistent review window without changing the live recording plot.
                self.review = QWidget()
                self.review.setWindowTitle("Recorded ECG · " + path.name)
                self.review.resize(900, 340)
                b = QVBoxLayout(self.review)
                b.addWidget(label("RECORDED ECG · FIRST 8 SECONDS", "eyebrow"))
                trace = Trace()
                trace.points = samples
                b.addWidget(trace)
                self.review.show()

        self.background(work, done, "Verifying checksums and sequence numbers…")

    def recover(self):
        if self.current_path:
            path = self.current_path
            self.background(
                lambda: recover_session(path),
                lambda _: self.recovered(),
                "Recovering committed chunks…",
            )

    def recovered(self):
        self.refresh_sessions()
        self.inspect()

    def derive(self):
        if self.current_path:
            path = self.current_path
            self.background(
                lambda: recalculate(path),
                lambda p: self.message.setText("Derived respiration saved: " + str(p)),
                "Recalculating from original ECG…",
            )

    def export(self):
        if not self.current_path:
            return
        path = self.current_path
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export session",
            str(self.root / (path.name + ".zip")),
            "ZIP archive (*.zip)",
        )
        if name:
            if not name.lower().endswith(".zip"):
                name += ".zip"
            self.background(
                lambda: export_session(path, name),
                lambda r: self.message.setText(
                    f'Exported {name} · {len(r["errors"])} integrity issue(s) documented in verification.json.'
                ),
                "Exporting raw data, derived data, events and CSV…",
            )

    def tick(self):
        """Collect finished jobs and render the latest acquisition snapshot.

        Callbacks run here on the Qt thread, where widget updates are safe.
        """
        if self.shutdown_done:
            return
        for job in self.jobs[:]:
            future, callback, control = job
            if not future.done():
                continue
            self.jobs.remove(job)
            if control:
                self.busy = False
            try:
                result = future.result()
                if callback:
                    callback(result)
            except Exception as exc:
                self.message.setText(str(exc))
                QMessageBox.warning(self, "Action could not complete", str(exc))
        if self.sound_future is not None and self.sound_future.done():
            self.sound_future = None
        state = self.engine.snapshot()
        recording = state["recording"]
        connected = self.engine.want_connection
        self.connection.setText(
            state["connection"]
            + (
                "  ·  Battery " + str(state["battery"]) + "%"
                if state["battery"] is not None
                else ""
            )
        )
        self.pill.setText(
            ("● RECORDING" if recording else "OFFLINE  ·  LOCAL STORAGE")
            + ("  /  SIMULATOR" if state["simulated"] else "")
        )
        self.hr.setText(str(state["hr"]) if state["hr"] is not None else "—")
        self.resp.setText(
            str(state["respiration"]) if state["respiration"] is not None else "—"
        )
        seconds = int(state["duration"])
        self.duration.setText(f"{seconds//60:02d}:{seconds%60:02d}")
        self.trace.points = state["ecg"]
        self.trace.update()
        self.quality.setText(state["quality"])
        self.write.setText(
            state["write"]
            + (
                "  ·  DROPOUT — reconnecting, session remains open"
                if state["dropout"]
                else ""
            )
        )
        self.resp_note.setText(
            "Derived respiration: "
            + state["resp_status"]
            + ". Not a directly measured respiratory signal."
        )
        if state["error"]:
            self.message.setText(state["error"])
        self.combo.setEnabled(not connected and not self.busy)
        self.sensor_note.setText(
            "SIMULATOR · Synthetic signals for testing only."
            if state["simulated"]
            else (
                "Device: " + state["device"]
                if connected
                else "Wear the moistened strap. Connect, then inspect the trace before recording."
            )
        )
        self.scan_button.setEnabled(
            not connected and not self.busy and self.engine.session is None
        )
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setEnabled(
            not self.busy and not recording and self.engine.session is None
        )
        self.demo_button.setEnabled(
            not connected and not self.busy and self.engine.session is None
        )
        self.start_button.setEnabled(
            state["ready"]
            and not recording
            and not self.busy
            and self.engine.session is None
            and (self.participant.currentData() is not None or state["simulated"])
        )
        self.stop_button.setEnabled(
            (recording or self.engine.session is not None) and not self.busy
        )
        self.participant.setEnabled(not recording and not self.busy and self.engine.session is None)
        self.root_button.setEnabled(not recording and not self.busy and self.engine.session is None)
        self.registry_button.setEnabled(not recording and not self.busy and self.engine.session is None)
        for control in self.phase_buttons:
            control.setEnabled(recording and not self.busy)
        self.sound_controls.setVisible(recording)
        pending_sound = recording and self.sound_future is not None and not self.sound_future.done()
        selected_sound = self.requested_sound if pending_sound else state["sound_label"]
        self.sound_state.setText("Sound: " + (state["sound_label"].title() if recording else "Silence")
                                + (" · saving " + self.requested_sound.title() + "…" if pending_sound else ""))
        for value, control in self.sound_buttons.items():
            control.setChecked(recording and selected_sound == value)
            control.setEnabled(recording and not self.busy and not self.closing)
        self.drop_button.setVisible(state["simulated"])
        self.drop_button.setEnabled(
            connected and not state["dropout"] and not self.busy
        )
        tools_ready = (
            self.current_path is not None
            and not self.jobs
            and not recording
            and self.engine.session is None
        )
        for control in (
            self.verify_button,
            self.recover_button,
            self.derive_button,
            self.export_button,
        ):
            control.setEnabled(tools_ready)
        self.analysis_button.setEnabled(self.current_path is not None and not self.jobs and not self.closing)

        # Close the window only after saving and other queued work finish.
        if self.closing and not self.jobs:
            self.timer.stop()
            if hasattr(self, "review"):
                self.review.close()
            for window in list(self.analysis_windows.values()):
                window.close()
            self.shutdown_done = True
            self.engine.loop.call_soon_threadsafe(self.engine.loop.stop)
            self.engine.thread.join(timeout=3)
            self.pool.shutdown(wait=False)
            self.close()

    def closeEvent(self, event):
        """Wait for asynchronous saving and shutdown before accepting a close."""
        if self.shutdown_done:
            event.accept()
            return
        if self.closing:
            event.ignore()
            return
        if self.engine.snapshot()["recording"]:
            choice = QMessageBox.question(
                self,
                "Stop recording and quit?",
                "The current session will be stopped and saved before closing.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                event.ignore()
                return
        self.closing = True
        self.action(self.engine.shutdown(), message="Saving and closing…")
        event.ignore()


def app_root():
    """Locate writable app storage beside the bundle or the source directory."""
    if getattr(sys, "frozen", False):
        # .app/Contents/MacOS/executable -> folder containing the bundle.
        return Path(sys.executable).resolve().parents[3]
    return Path(__file__).resolve().parents[1]


def main():
    """Configure Qt, acquire the single-instance lock, and start the window."""
    app = QApplication(sys.argv)
    app.setApplicationName("Svaram H10")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    default_root = app_root().parent / "svaram-data" / "data"
    root = Path(QSettings("Shobotics", "Svaram H10").value("study_root", str(default_root)))
    if "--data-root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--data-root") + 1])
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / ".svaram.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(
            None,
            "Svaram is already open",
            "An instance is already using this app folder. Use the existing window.",
        )
        return 0
    try:
        window = Window(root)
    except OSError as exc:
        QMessageBox.critical(
            None,
            "Storage folder unavailable",
            f"Cannot write sessions next to the app: {exc}",
        )
        return 1
    window.show()
    if "--session" in sys.argv:
        window.current_path = Path(sys.argv[sys.argv.index("--session") + 1])
        window.tabs.setCurrentIndex(1)
        QTimer.singleShot(300, window.open_analysis)
    if "--demo" in sys.argv:
        QTimer.singleShot(300, window.demo)
    if "--screenshot" in sys.argv:
        filename = sys.argv[sys.argv.index("--screenshot") + 1]
        QTimer.singleShot(3500, lambda: window.grab().save(filename))
    if "--smoke" in sys.argv:
        QTimer.singleShot(4500, window.close)
    return app.exec()
