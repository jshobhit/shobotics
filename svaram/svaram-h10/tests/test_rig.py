import asyncio
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tracemalloc
import zipfile
import pytest
from h10rig.storage import (
    SessionWriter,
    read_chunk,
    verify_session,
    recover_session,
    export_session,
    iter_rows,
    atomic_json,
)
from h10rig.protocol import (
    decode_hr,
    decode_pmd,
    SensorClock,
    start_command,
    parse_settings,
    ProtocolError,
)
from h10rig.respiration import derive, recalculate, WindowProcessor


class Clock:
    def __init__(self):
        self.t = 1000

    def __call__(self):
        return self.t

    def advance(self, n=5.1):
        self.t += n


def session(tmp_path):
    c = Clock()
    w = SessionWriter(tmp_path, "test-device", clock=c)
    return w, c


def add(w, c, i=0):
    w.add("ecg", [{"t": i + j / 130, "uv": j - 5} for j in range(130)])
    c.advance()
    w.flush()


@pytest.mark.parametrize("value", [-8388608, -1, 0, 1, 8388607])
def test_ecg_signed_24(value):
    data = (
        bytes([0])
        + (123456789).to_bytes(8, "little")
        + bytes([0])
        + value.to_bytes(3, "little", signed=True)
    )
    assert decode_pmd(data) == ("ecg", 123456789, [{"uv": value}])


@pytest.mark.parametrize(
    "frame,width,values",
    [
        (0, 1, (-128, 0, 127)),
        (1, 2, (-32768, 1000, 32767)),
        (2, 3, (-8388608, 0, 8388607)),
    ],
)
def test_acc_frames(frame, width, values):
    data = (
        bytes([2])
        + bytes(8)
        + bytes([frame])
        + b"".join(v.to_bytes(width, "little", signed=True) for v in values)
    )
    assert list(decode_pmd(data)[2][0].values()) == list(values)


def test_hr_16bit_energy_rr():
    data = (
        bytes([0x1F])
        + int(300).to_bytes(2, "little")
        + int(42).to_bytes(2, "little")
        + int(1024).to_bytes(2, "little")
        + int(853).to_bytes(2, "little")
    )
    obj = decode_hr(data)
    assert (
        obj["bpm"] == 300
        and obj["energy_kj"] == 42
        and obj["rr_ticks"] == [1024, 853]
        and obj["contact"]
    )


def test_bad_packets_and_contact():
    assert decode_hr(bytes([0, 70]))["contact"] is None
    assert decode_hr(bytes([4, 70]))["contact"] is False
    for packet in (b"", bytes(11), bytes([0]) + bytes(8) + bytes([2, 0])):
        with pytest.raises(ProtocolError):
            decode_pmd(packet)
    with pytest.raises(ProtocolError):
        decode_hr(bytes([0x10, 70, 1]))


def test_settings_and_start():
    settings = parse_settings(bytes([0, 2, 25, 0, 50, 0, 1, 1, 16, 0, 2, 1, 8, 0]))
    assert start_command(2, settings) == bytes(
        [2, 2, 0, 1, 50, 0, 1, 1, 16, 0, 2, 1, 8, 0]
    )
    with pytest.raises(ProtocolError):
        start_command(0, settings)


def test_sensor_timeline_gap_and_shared_anchor():
    clock = SensorClock()
    stamp = 700_000_000_000_000_000
    times, stamps, events = clock.map("ecg", stamp, 130, 130, 100)
    assert times[-1] == 100 and stamps[-1] == stamp
    acc, _, _ = clock.map("accelerometer", stamp + 100_000_000, 5, 50, 100.2)
    assert abs(acc[-1] - 100.1) < 1e-7
    after, _, events = clock.map("ecg", stamp + 21_000_000_000, 130, 130, 121)
    assert after[0] > 120 and any(k == "missing_samples" for k, _ in events)
    # No interpolation across the 20-second dropout.
    assert after[0] - times[-1] > 20


def test_flush_atomic_checksum_and_raw_immutable(tmp_path):
    w, c = session(tmp_path)
    add(w, c)
    p = w.path / "raw/ecg/00000000.json"
    before = p.read_bytes()
    assert read_chunk(p)["sample_count"] == 130
    with pytest.raises(FileExistsError):
        atomic_json(p, {}, exclusive=True)
    assert p.read_bytes() == before
    # Failed publication leaves a temp that the verifier must report.
    assert any("temporary" in e["error"] for e in verify_session(w.path)["errors"])


def test_corrupt_missing_duplicate_all_reported(tmp_path):
    w, c = session(tmp_path)
    for i in range(3):
        add(w, c, i * 6)
    w.finish()
    p = w.path / "raw/ecg/00000001.json"
    p.chmod(0o644)
    p.write_text("{broken")
    shutil.copy(w.path / "raw/ecg/00000000.json", w.path / "raw/ecg/duplicate.json")
    (w.path / "raw/ecg/00000002.json").unlink()
    report = verify_session(w.path)
    errors = " ".join(e["error"] for e in report["errors"])
    assert "duplicate" in errors and "missing" in errors
    assert len(list(iter_rows(w.path, "ecg"))) == 130


def test_crash_subprocess_and_recovery(tmp_path):
    code = (
        """from h10rig.storage import SessionWriter
import os
w=SessionWriter(r"%s",'crash-test')
w.add('ecg',[{'t':0,'uv':123}]); w.flush(True)
w.add('ecg',[{'t':1,'uv':456}])
os._exit(7)
"""
        % tmp_path
    )
    proc = subprocess.run([sys.executable, "-c", code])
    assert proc.returncode == 7
    path = next(tmp_path.iterdir())
    assert (path / "active.json").exists()
    report = recover_session(path)
    assert report["valid_chunks"] == 1
    assert list(iter_rows(path, "ecg")) == [{"t": 0, "uv": 123}]
    assert json.loads((path / "manifest.json").read_text())["status"] == "recovered"
    assert not (path / "active.json").exists()


def test_orphan_committed_chunk_is_recovered(tmp_path):
    w, c = session(tmp_path)
    initial = (w.path / "manifest.json").read_bytes()
    add(w, c)
    (w.path / "manifest.json").write_bytes(initial)
    assert verify_session(w.path)["errors"]
    report = recover_session(w.path)
    assert report["valid_chunks"] == 1 and not report["errors"]


def test_full_export_preserves_corrupt_sources_and_csv(tmp_path):
    w, c = session(tmp_path / "sessions")
    add(w, c)
    w.finish()
    dest = tmp_path / "export.zip"
    result = export_session(w.path, dest)
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        prefix = w.path.name + "/"
        assert prefix + "csv/ecg.csv" in z.namelist()
        assert prefix + "raw/ecg/00000000.json" in z.namelist()
        assert prefix + "verification.json" in z.namelist()
    assert not result["errors"]


def test_no_export_while_recording(tmp_path):
    w, c = session(tmp_path / "sessions")
    with pytest.raises(ValueError):
        export_session(w.path, tmp_path / "x.zip")


def synthetic(duration=70):
    rows = []
    for i in range(duration * 130):
        t = i / 130
        phase = (t % (5 / 6)) / (5 / 6)
        x = (
            1100
            * (1 + 0.22 * math.sin(2 * math.pi * 0.25 * t))
            * math.exp(-(((phase - 0.2) / 0.018) ** 2))
            - 220 * math.exp(-(((phase - 0.24) / 0.025) ** 2))
            + 150 * math.exp(-(((phase - 0.5) / 0.09) ** 2))
        )
        rows.append({"t": t, "uv": round(x)})
    return rows


def test_respiration_known_modulation_and_gap_rejection():
    rows = synthetic(60)
    r = derive(rows)
    assert r["status"] == "ok" and abs(r["rate_bpm"] - 15) < 1
    assert r["derived"] and r["signal"]
    for row in rows[3900:]:
        row["t"] += 20
    assert derive(rows)["rate_bpm"] is None


def test_flatline_not_reported_as_respiration():
    assert derive([{"t": i / 130, "uv": 0} for i in range(7800)])["rate_bpm"] is None


def test_regeneration_reproducible_raw_unchanged(tmp_path):
    w, c = session(tmp_path)
    rows = synthetic(70)
    for i in range(0, len(rows), 650):
        w.add("ecg", rows[i : i + 650])
        c.advance()
        w.flush()
    w.finish()
    before = {p: p.read_bytes() for p in (w.path / "raw").rglob("*.json")}
    first = recalculate(w.path)
    second = recalculate(w.path)
    assert (first / "000000.json").read_bytes() == (second / "000000.json").read_bytes()
    assert all(p.read_bytes() == content for p, content in before.items())


def test_sixty_minute_recording_bounded_buffers(tmp_path):
    w, c = session(tmp_path)
    tracemalloc.start()
    # Deliver a full hour of ECG/motion in 5s batches, advancing a monotonic test clock.
    for second in range(0, 3600, 5):
        w.add("ecg", [{"t": second + i / 130, "uv": i % 200 - 100} for i in range(650)])
        w.add(
            "accelerometer",
            [
                {"t": second + i / 50, "x_mg": 0, "y_mg": 0, "z_mg": 1000}
                for i in range(250)
            ],
        )
        c.advance(5)
        w.flush()
        assert all(not b for b in w.buffers.values())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    w.finish()
    assert peak < 25_000_000
    m = json.loads((w.path / "manifest.json").read_text())
    assert (
        m["streams"]["ecg"]["samples"] == 468000
        and m["streams"]["accelerometer"]["samples"] == 180000
    )
    assert verify_session(w.path)["valid_chunks"] == 1440


def test_engine_simulator_record_stop_and_ui_independence(tmp_path):
    from h10rig.engine import Engine

    e = Engine(tmp_path)
    try:
        e.submit(e.connect(simulated=True)).result(3)
        time.sleep(0.5)
        assert e.snapshot()["ready"]
        path = Path(e.submit(e.start("test")).result(3))
        time.sleep(0.4)
        e.submit(e.simulate_dropout(0.3)).result(3)
        assert e.snapshot()["recording"] and e.snapshot()["dropout"]
        time.sleep(0.7)
        assert not e.snapshot()["dropout"]
        e.submit(e.stop()).result(4)
        assert verify_session(path)["valid_chunks"] > 0
        events = (path / "events/connection.jsonl").read_text()
        assert "disconnected" in events and "reconnected" in events
    finally:
        e.submit(e.shutdown()).result(4)
        e.loop.call_soon_threadsafe(e.loop.stop)
        e.thread.join(3)
