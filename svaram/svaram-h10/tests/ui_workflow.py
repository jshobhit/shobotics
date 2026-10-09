import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile

qa_root = Path(tempfile.mkdtemp(prefix="svaram-ui-qa-"))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from h10rig.ui import Window, STYLE
from h10rig.storage import iter_rows, export_session

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)


def wait(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


w = Window(qa_root)
w.show()
w.tabs.setCurrentIndex(0)
w.demo_button.click()
wait(800)
assert w.start_button.isEnabled()
w.participant.setText("QA-SIMULATED")
w.start_button.click()
wait(2000)
assert w.engine.snapshot()["recording"]
w.timer.stop()
wait(5500)
path = Path(w.engine.snapshot()["session"])
assert list(
    (path / "raw/ecg").glob("*.json")
), "Storage must continue with display timer disabled"
w.timer.start(100)
wait(400)
assert (
    w.trace.geometry().bottom() < w.quality.geometry().top()
), "Trace must not overlap captions"
w.grab().save(str(qa_root / "ui-recording.png"))
w.drop_button.click()
wait(400)
assert w.engine.snapshot()["dropout"] and w.engine.snapshot()["recording"]
w.grab().save(str(qa_root / "ui-dropout.png"))
wait(20800)
assert not w.engine.snapshot()["dropout"] and w.engine.snapshot()["recording"]
w.stop_button.click()
wait(1600)
assert not w.engine.snapshot()["recording"] and not (path / "active.json").exists()
rows = list(iter_rows(path, "ecg"))
gap = max(b["t"] - a["t"] for a, b in zip(rows, rows[1:]))
assert 19.5 < gap < 21, gap
w.tabs.setCurrentIndex(1)
w.refresh_sessions()
w.table.selectRow(0)
w.inspect()
wait(1000)
w.grab().save(str(qa_root / "ui-library.png"))
report = export_session(path, qa_root / "qa-export.zip")
assert not report["errors"]
print(
    json.dumps(
        {
            "session": path.name,
            "ecg_gap_s": gap,
            "valid_chunks": report["valid_chunks"],
            "issues": len(report["errors"]),
            "ui_independent_storage": True,
        }
    )
)
if hasattr(w, "review"):
    w.review.close()
w.close()
wait(600)
assert w.shutdown_done
