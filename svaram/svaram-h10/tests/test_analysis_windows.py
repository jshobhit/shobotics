"""Offscreen Qt workflow: labels and analysis do not control each other's lifetime."""

import os
import time
import json
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QStyle, QStyleOptionButton

from h10rig.storage import SessionWriter
from h10rig import ui


def test_independent_windows_and_live_label_controls(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(ui.STYLE)
    monkeypatch.setattr(ui, "app_root", lambda: tmp_path)
    errors = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: errors.append(args[-1]))

    def until(predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert time.monotonic() < deadline, errors
            time.sleep(0.01)
        assert not errors

    paths = []
    for _ in range(2):
        clock = [0]
        writer = SessionWriter(tmp_path / "sessions", "fixture", clock=lambda: clock[0])
        writer.add("hr", [{"t": 1, "bpm": 60}, {"t": 8, "bpm": 80}])
        writer.set_sound_label("fire", 3)
        clock[0] = 10
        writer.finish()
        paths.append(writer.path.resolve())
    window = ui.Window(tmp_path)
    window.show()
    try:
        assert window.sound_controls.isHidden()
        for path in paths:
            window.current_path = path
            window.open_analysis()
            until(lambda: path in window.analysis_windows and not window.jobs)
        first, second = [window.analysis_windows[p] for p in paths]
        assert first.isWindow() and second.isWindow()
        first.centralWidget().from_spin.setValue(2)
        first.centralWidget().apply_range()
        assert second.centralWidget().start == 0
        assert first.centralWidget().hr_chart.sound_intervals == [(0, 3, "silence"), (3, 10, "fire")]
        window.current_path = paths[0]
        window.open_analysis()
        assert len(window.analysis_windows) == 2
        assert window.analysis_windows[paths[0]] is first
        assert not first.grab().isNull()  # Exercise chart paint paths.

        window.demo_button.click()
        until(lambda: window.start_button.isEnabled() and not window.busy)
        window.start_button.click()
        assert not window.start_button.isEnabled()
        until(lambda: window.engine.snapshot()["recording"] and not window.busy)
        assert not window.sound_controls.isHidden()
        # All clicks in a fast sequence must be recorded, without locking Stop.
        window.sound_buttons["fire"].click()
        assert window.stop_button.isEnabled()
        window.sound_buttons["water"].click()
        window.sound_buttons["water"].click()
        until(lambda: not window.jobs)
        assert window.engine.snapshot()["sound_label"] == "silence"
        recording_path = Path(window.engine.snapshot()["session"])
        transitions = [json.loads(line)["label"]["value"] for line in
                       (recording_path / "events/sound_labels.jsonl").read_text().splitlines()]
        assert transitions == ["silence", "fire", "water", "silence"]
        assert window.stop_button.isEnabled()
        assert window.sound_controls.mapTo(window, window.sound_controls.rect().bottomLeft()).y() < window.trace.mapTo(window, window.trace.rect().topLeft()).y()
        for value in ("fire", "water", "water"):
            target = "silence" if window.engine.snapshot()["sound_label"] == value else value
            window.sound_buttons[value].click()
            until(lambda: window.engine.snapshot()["sound_label"] == target and not window.jobs)
            assert window.sound_state.text() == "Sound: " + target.title()
            assert sum(b.isChecked() for b in window.sound_buttons.values()) == (target != "silence")

        window.sound_buttons["earth"].click()
        until(lambda: not window.jobs)
        for width, height in ((940, 740), (1140, 880)):
            window.resize(width, height)
            app.processEvents()
            for control in (window.start_button, window.stop_button, *window.sound_buttons.values()):
                option = QStyleOptionButton()
                control.initStyleOption(option)
                contents = control.style().subElementRect(QStyle.SE_PushButtonContents, option, control)
                assert contents.height() >= control.fontMetrics().height(), control.text()
                assert contents.width() >= control.fontMetrics().horizontalAdvance(control.text().replace("&&", "&")), control.text()
            screenshot = tmp_path / f"recording-{width}.png"
            assert window.grab().save(str(screenshot))
            print(f"UI screenshot: {screenshot}")

        first.close()
        until(lambda: paths[0] not in window.analysis_windows)
        assert window.engine.snapshot()["recording"]
        assert second.isVisible()
        window.current_path = paths[0]
        window.open_analysis()
        until(lambda: paths[0] in window.analysis_windows and not window.jobs)
        assert window.engine.snapshot()["recording"]
        # Stop stays actionable even immediately after another label click.
        window.sound_buttons["space"].click()
        window.stop_button.click()
        assert not window.stop_button.isEnabled()
        until(lambda: not window.engine.snapshot()["recording"] and not window.jobs)
        assert window.sound_controls.isHidden()
        transitions = [json.loads(line)["label"]["value"] for line in
                       (recording_path / "events/sound_labels.jsonl").read_text().splitlines()]
        assert transitions[-1] == "space"
        assert json.loads((recording_path / "manifest.json").read_text())["status"] == "complete"
    finally:
        window.engine.submit(window.engine.stop()).result(6)
        window.close()
        until(lambda: window.shutdown_done)
        until(lambda: not window.analysis_windows)
