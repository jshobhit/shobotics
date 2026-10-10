"""Main window: layout, the frame loop, and the recording flow.

Threading
    bleak runs on its own thread (ble.PolarH10). Its callbacks emit Qt signals
    on `BleBridge`, so every packet is handled here on the UI thread, in
    arrival order, already stamped with its true arrival time.

Frame loop
    One precise 16 ms timer advances the ECG sweep and repaints both charts.
    Rolling RMSSD is sampled once a second; vitals, label timers and the clock
    refresh at 2 Hz. Signal data lives in `Session` (plain lists), never in
    widgets.
"""

from __future__ import annotations

import math
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..ble import BluetoothUnavailable, PolarH10, SensorNotFound, decode_ecg, parse_hr
from ..config import HOTKEYS, HRV_WINDOWS, VIEW_WINDOWS
from ..export import export_bytes, file_name
from ..session import Session
from . import theme
from .ecg_sweep import EcgSweep
from .label_rail import LabelRail
from .new_recording import SETTINGS, NewRecordingDialog
from .review import ReviewDialog
from .title_bar import TitleBar
from .trend_chart import TrendChart
from .vitals import Vitals
from .widgets import Button, Segmented, box, clock_text

FRAME_MS = 16
COMPACT_BELOW = 960  # px: stack to one column and scroll
ECG_BOX_H = 164
RAIL_W = 300
BLINK_S = 1.2
ECG_META = "130 Hz · 25 mm/s · 10 mm/mV"
FIX_STRAP = "Moisten the strap electrodes, wear it, and try again."
EXPORT_FILTERS = {
    "csv": "CSV (*.csv)",
    "ecg": "CSV (*.csv)",
    "json": "JSON (*.json)",
    "zip": "Zip archive (*.zip)",
}


def now() -> float:
    return time.monotonic()


def reduce_motion() -> bool:
    """macOS "Reduce motion" setting; False elsewhere or if unavailable."""
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSWorkspace

        return bool(
            NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
        )
    except Exception:
        return False


class WakeLock:
    """Keep the display awake while recording (macOS `caffeinate`).

    Failure is ignored: the recording never depends on it.
    """

    def __init__(self):
        self._proc = None

    def acquire(self):
        if sys.platform == "darwin" and self._proc is None:
            try:
                cmd = ["caffeinate", "-di", "-w", str(QApplication.applicationPid())]
                self._proc = subprocess.Popen(cmd)
            except OSError:
                self._proc = None

    def release(self):
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None


class BleBridge(QObject):
    """Qt signals fed from the BLE thread; delivered on the UI thread."""

    state = Signal(str)
    hr = Signal(float, object)
    ecg = Signal(float, object)
    ecg_status = Signal(bool)
    lost = Signal()
    error = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sonorium")
        self.resize(1280, 800)
        self.setMinimumSize(720, 520)

        self.session = Session()
        self.conn_state = "disconnected"  # disconnected | connecting | connected
        self.contact: bool | None = None
        self.known_device = False  # a strap has connected at least once
        self.view_s = VIEW_WINDOWS[0][1]
        self.hrv_win = HRV_WINDOWS[0]
        self.last_good_beat = -1.0  # for the heart pulse
        self.last_hrv = self.last_vitals = 0.0
        self.compact = None
        self.motion = not reduce_motion()
        self.wake = WakeLock()

        self.bridge = BleBridge()
        self.bridge.state.connect(self.on_ble_state)
        self.bridge.hr.connect(self.on_hr)
        self.bridge.ecg.connect(self.on_ecg)
        self.bridge.ecg_status.connect(self.on_ecg_status)
        self.bridge.lost.connect(self.on_ble_lost)
        self.bridge.error.connect(self.on_ble_error)
        b = self.bridge
        self.ble = PolarH10(
            b.state.emit,
            b.hr.emit,
            b.ecg.emit,
            b.ecg_status.emit,
            b.lost.emit,
            b.error.emit,
        )

        self._build()
        self._wire()
        self.render()
        self.set_connected("disconnected")

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.frame)
        self.timer.start(FRAME_MS)

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        page = QVBoxLayout(root)
        page.setContentsMargins(12, 0, 12, 12)
        page.setSpacing(10)

        self.bar = TitleBar()
        page.addWidget(self.bar)

        self.body = QBoxLayout(QBoxLayout.LeftToRight)
        self.body.setSpacing(10)
        page.addLayout(self.body, 1)

        left = QVBoxLayout()
        left.setSpacing(10)
        self.body.addLayout(left, 1)

        # Alert strip, shown only when something needs attention.
        self.alert = QFrame()
        self.alert.setObjectName("alert")
        a = QHBoxLayout(self.alert)
        a.setContentsMargins(12, 7, 12, 7)
        a.setSpacing(10)
        self.alert_text = QLabel()
        self.alert_text.setWordWrap(True)
        a.addWidget(self.alert_text, 1)
        self.reconnect = Button("Reconnect")
        a.addWidget(self.reconnect)
        self.alert.hide()
        left.addWidget(self.alert)

        self.vitals = Vitals(reduce_motion=not self.motion)
        left.addWidget(self.vitals)

        self.chart_box, head, body = box("Heart rate and HRV")
        self.hrv_seg = Segmented(
            "HRV", [(f"{w} s", w) for w in HRV_WINDOWS], self.hrv_win
        )
        self.view_seg = Segmented("View", list(VIEW_WINDOWS), self.view_s)
        head.addWidget(self.hrv_seg)
        head.addWidget(self.view_seg)
        self.trend = TrendChart()
        self.trend.session = self.session
        body.addWidget(self.trend, 1)
        left.addWidget(self.chart_box, 1)

        self.ecg_box, head, body = box("Live ECG")
        self.ecg_meta = QLabel(ECG_META)
        font = theme.mono_font(10.5, QFont.DemiBold)
        font.setCapitalization(QFont.AllUppercase)
        self.ecg_meta.setFont(font)
        self.ecg_meta.setProperty("tone", "3")
        head.addWidget(self.ecg_meta)
        self.ecg = EcgSweep()
        body.addWidget(self.ecg, 1)
        self.ecg_box.setFixedHeight(ECG_BOX_H)
        left.addWidget(self.ecg_box)

        self.rail = LabelRail()
        self.body.addWidget(self.rail)

        # The page scrolls only when the window is too small for it.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(root)
        self.setCentralWidget(self.scroll)

        self.new_dialog = NewRecordingDialog(
            self,
            is_connected=lambda: self.conn_state == "connected",
            has_unsaved=lambda: self.session.phase == "done"
            and not self.session.exported,
        )
        self.review_dialog = ReviewDialog(self)
        self.set_compact(False)

    def _wire(self) -> None:
        self.bar.connect.clicked.connect(lambda: self.connect_ble(False))
        self.new_dialog.connect_requested.connect(lambda: self.connect_ble(False))
        self.reconnect.clicked.connect(lambda: self.connect_ble(True))
        self.bar.new.clicked.connect(self.open_new)
        self.bar.review.clicked.connect(self.open_review)
        self.bar.stop.held.connect(self.stop_recording)
        self.rail.toggled.connect(self.toggle)
        self.hrv_seg.changed.connect(self.set_hrv_window)
        self.view_seg.changed.connect(self.set_view)
        self.new_dialog.start_requested.connect(self.start_recording)
        self.review_dialog.export_requested.connect(self.save)

        # Label hotkeys and N. A window shortcut never fires while a modal
        # dialog has focus, and the main window has no text fields.
        for key, label in HOTKEYS.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.setAutoRepeat(False)
            sc.activated.connect(lambda label=label: self.toggle(label))
        sc = QShortcut(QKeySequence("N"), self)
        sc.setAutoRepeat(False)
        sc.activated.connect(self.open_new)

        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda *_: self.restyle())

    # ---------------------------------------------------------------- layout

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.set_compact(self.width() < COMPACT_BELOW)

    def set_compact(self, compact: bool) -> None:
        """Below 960 px: one column, chart at a fixed height, page scrolls."""
        if compact == self.compact:
            return
        self.compact = compact
        self.body.setDirection(
            QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight
        )
        self.bar.set_compact(compact)
        self.vitals.set_compact(compact)
        self.rail.set_compact(compact)
        if compact:
            self.rail.setMinimumWidth(0)
            self.rail.setMaximumWidth(16777215)
            self.chart_box.setFixedHeight(360)
        else:
            self.rail.setFixedWidth(RAIL_W)
            self.chart_box.setMinimumHeight(0)
            self.chart_box.setMaximumHeight(16777215)

    def restyle(self) -> None:
        """System light/dark switch: new tokens, re-tinted logo and paper."""
        theme.apply(QApplication.instance())
        self.bar.logo.restyle()
        self.ecg.restyle()
        for w in QApplication.allWidgets():
            w.update()

    # ------------------------------------------------------------------- loop

    def frame(self) -> None:
        t = now()
        self.ecg.sweep.advance(t)
        if t - self.last_hrv >= 1:
            self.last_hrv = t
            self.session.sample_hrv(t, self.conn_state == "connected")
        if t - self.last_vitals >= 0.5:
            self.last_vitals = t
            self.render_vitals(t)
        if self.motion:
            self.vitals.heart.tick(t, self.last_good_beat)
            if self.session.recording:
                phase = 2 * math.pi * (t % BLINK_S) / BLINK_S
                self.bar.rec.dot_alpha = 0.6 + 0.4 * math.cos(phase)
                self.bar.rec.update()
        self.trend.now = t
        if self.trend.needs_redraw(t):
            self.trend.update()
        self.ecg.update()

    def render_vitals(self, t: float) -> None:
        s = self.session
        connected = self.conn_state == "connected"
        ref = s.reference(t)
        self.trend.ref = ref
        self.vitals.refresh(s, t, connected, self.hrv_win, ref)
        if connected:
            self.set_connected("connected")  # contact can change any time
        if s.recording:
            clock = s.rel(t)
        elif s.phase == "done":
            clock = s.rel(s.t_end)
        else:
            clock = 0
        self.bar.clock.setText(clock_text(clock))
        if s.recording:
            self.rail.refresh(s, t)

    def render(self) -> None:
        """Phase-dependent chrome: IDs, recording pill, action buttons, rail."""
        s = self.session
        live, done = s.recording, s.phase == "done"
        self.bar.show_session(s.ids, s.docs)
        self.bar.rec.set("Recording" if live else "Stopped" if done else "Idle",
                         "rec" if live else "")  # fmt: skip
        self.bar.rec.dot_alpha = 1.0
        self.bar.stop.setVisible(live)
        self.bar.review.setVisible(done)
        self.bar.new.setVisible(not live)
        self.rail.refresh(s, now())
        self.render_vitals(now())

    # -------------------------------------------------------------------- BLE

    def connect_ble(self, reuse: bool) -> None:
        if self.conn_state == "disconnected":
            self.ble.connect(reuse)

    def set_connected(self, state: str) -> None:
        self.conn_state = state
        if state == "connecting":
            text, tone = "Connecting…", "warn"
        elif state == "connected":
            touching = self.contact is not False
            text = (
                "Polar H10 · skin contact"
                if touching
                else "Polar H10 · no skin contact"
            )
            tone = "ok" if touching else "warn"
        else:
            text, tone = "Polar H10 not connected", ""
        self.bar.sensor.set(text, tone)
        connected = state == "connected"
        self.bar.connect.setVisible(not connected)
        self.bar.connect.setEnabled(state != "connecting")
        self.new_dialog.connect_btn.setEnabled(state != "connecting")
        self.trend.connected = self.ecg.connected = connected
        self.new_dialog.refresh()

    def on_ble_state(self, state: str) -> None:
        was = self.conn_state
        self.set_connected(state)
        if state == "connected":
            self.known_device = True
            self.hide_alert()
            if self.session.recording and was != "connected":
                self.session.log("signal", "Sensor reconnected", now())

    def on_hr(self, arrival: float, data: bytes) -> None:
        contact, rr = parse_hr(data)
        if contact is not None:
            self.contact = contact
        beats = self.session.add_hr_packet(arrival, rr)
        if any(not b.art for b in beats):
            self.last_good_beat = now()

    def on_ecg(self, arrival: float, data: bytes) -> None:
        samples = decode_ecg(data)
        if samples:
            self.session.add_ecg(arrival, samples)
            self.ecg.sweep.push(samples)

    def on_ecg_status(self, ok: bool) -> None:
        self.ecg_meta.setText(ECG_META if ok else "ECG stream unavailable")

    def on_ble_lost(self) -> None:
        s = self.session
        if s.recording:
            t = now()
            s.log("signal", "Sensor lost", t)
            self.show_alert(
                f"Polar H10 disconnected at {clock_text(s.rel(t))}. "
                "Recording continues; beats resume on reconnect.",
                reconnect=True,
            )
        else:
            self.show_alert("Polar H10 disconnected.", reconnect=True)

    def on_ble_error(self, exc: Exception) -> None:
        if isinstance(exc, BluetoothUnavailable):
            msg = (
                "Bluetooth isn't available here. Turn Bluetooth on and allow "
                "Sonorium in System Settings › Privacy & Security › Bluetooth."
            )
        elif isinstance(exc, SensorNotFound):
            msg = "Couldn't find a Polar H10. " + FIX_STRAP
        else:
            msg = f"Couldn't connect: {exc}. " + FIX_STRAP
        self.show_alert(msg, reconnect=self.known_device)

    def show_alert(self, msg: str, reconnect: bool = False) -> None:
        self.alert_text.setText(msg)
        self.reconnect.setVisible(reconnect)
        self.alert.show()

    def hide_alert(self) -> None:
        self.alert.hide()

    # ------------------------------------------------------------ recording

    def toggle(self, label: str) -> None:
        if self.session.recording:
            self.session.toggle(label, now())
            self.rail.refresh(self.session, now())

    def set_hrv_window(self, w: int) -> None:
        self.hrv_win = self.trend.hrv_win = w
        self.render_vitals(now())

    def set_view(self, seconds: int) -> None:
        self.view_s = self.trend.view_s = seconds

    def open_new(self) -> None:
        if self.session.recording or self.new_dialog.isVisible():
            return
        if self.review_dialog.isVisible():
            return
        self.new_dialog.show()
        self.new_dialog.raise_()

    def start_recording(self, ids: dict, docs: dict) -> None:
        self.session.start(ids, docs, now())
        self.wake.acquire()
        self.render()

    def stop_recording(self) -> None:
        if not self.session.recording:
            return
        self.session.stop(now())
        self.wake.release()
        self.render()
        self.open_review()

    def open_review(self) -> None:
        if self.session.phase != "done":
            return
        self.review_dialog.load(self.session)
        self.review_dialog.show()
        self.review_dialog.raise_()

    def save(self, kind: str) -> None:
        """Ask where to save, then write the export (the session is frozen)."""
        s = self.session
        settings = QSettings(*SETTINGS)
        folder = Path(
            str(settings.value("export_dir", "") or Path.home() / "Documents")
        )
        name = file_name(s, kind)
        path, _ = QFileDialog.getSaveFileName(
            self.review_dialog, "Save " + name, str(folder / name), EXPORT_FILTERS[kind]
        )
        if not path:
            self.review_dialog.set_note("Save cancelled.")
            return
        self.review_dialog.set_note("Preparing " + name + "…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            Path(path).write_bytes(export_bytes(s, kind))
        except OSError as exc:
            self.review_dialog.set_note(f"Couldn't save: {exc.strerror or exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        settings.setValue("export_dir", str(Path(path).parent))
        s.exported = True
        self.review_dialog.set_note("Saved " + Path(path).name + ".")
        self.new_dialog.refresh()

    # ---------------------------------------------------------------- close

    def closeEvent(self, e):
        s = self.session
        if s.recording:
            msg = "A recording is in progress. Quitting ends it without saving."
        elif s.phase == "done" and not s.exported:
            msg = "The last session wasn't saved. Quitting discards it."
        else:
            msg = None
        if msg:
            box_ = QMessageBox(self)
            box_.setIcon(QMessageBox.Warning)
            box_.setWindowTitle("Quit Sonorium?")
            box_.setText(msg)
            quit_btn = box_.addButton("Quit", QMessageBox.DestructiveRole)
            box_.addButton("Cancel", QMessageBox.RejectRole)
            box_.exec()
            if box_.clickedButton() is not quit_btn:
                e.ignore()
                return
        self.timer.stop()
        self.wake.release()
        self.ble.close()
        e.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Sonorium")
    app.setOrganizationName("Shobotics")
    app.setStyle("Fusion")
    theme.apply(app)
    window = MainWindow()
    window.show()
    return app.exec()
