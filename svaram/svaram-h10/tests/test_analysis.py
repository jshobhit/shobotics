import hashlib
import math
import pytest
from h10rig.analysis import SessionAnalysis, rr_metrics, coverage
from h10rig.storage import SessionWriter, atomic_json


def rr(values):
    return [
        {"t": i * 0.8, "ms": v, "index": i, "receipt": i * 0.8}
        for i, v in enumerate(values)
    ]


def test_rr_formula_reference():
    m = rr_metrics(rr([750, 850] * 20), 0, 32)
    assert m["rmssd"] == 100 and m["prr50"] == 100 and m["mean"] == 800
    assert (
        m["sdrr"] == pytest.approx(math.sqrt(40 * 2500 / 39)) and len(m["pairs"]) == 39
    )


def test_rejected_interval_never_bridged():
    data = rr([800] * 40)
    data[20]["ms"] = 4000
    m = rr_metrics(data, 0, 32)
    assert m["excluded"] == 1 and len(m["pairs"]) == 37 and m["rmssd"] == 0


def test_gap_nonmonotonic_and_range_edges_not_bridged():
    data = rr([800] * 40)
    for r in data[20:]:
        r["t"] += 20
        r["receipt"] += 20
    assert len(rr_metrics(data, 0, 80)["pairs"]) == 38
    assert rr_metrics(data, 0, 10)["rmssd"] is None
    data[1]["t"] = -1
    assert len(rr_metrics(data, 0, 80)["pairs"]) == 36


def test_coverage_union_not_double_count():
    assert coverage([[0, 5], [3, 7], [9, 10]], 0, 10) == 80
    assert coverage([[0, 5], [3, 7]], 4, 6) == 100


def make_session(tmp_path):
    w = SessionWriter(tmp_path, "fixture")
    w.add("ecg", [{"t": i / 130, "uv": i % 20} for i in range(1300)])
    w.add(
        "accelerometer",
        [{"t": i / 50, "x_mg": 0, "y_mg": 0, "z_mg": 1000} for i in range(500)],
    )
    w.add("hr", [{"t": 0, "bpm": 60}, {"t": 5, "bpm": 80}, {"t": 9, "bpm": 100}])
    w.add(
        "rr",
        [
            {"t": i * 0.8, "rr_ticks": 819, "host_receipt_monotonic_s": i * 0.8}
            for i in range(12)
        ],
    )
    w.finish()
    w.manifest["duration_s"] = 10
    w.save_manifest()
    return w.path


def fingerprints(path):
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.rglob("*")
        if p.is_file()
    }


def test_session_analysis_read_only_and_range_correct(tmp_path):
    path = make_session(tmp_path)
    before = fingerprints(path)
    m = SessionAnalysis(path)
    s = m.summary()
    assert s["hr"]["mean"] == 80 and s["hr"]["min"] == 60 and s["hr"]["max"] == 100
    assert s["ecg_coverage"] == pytest.approx(100) and s["motion_rms"] == 0
    assert m.summary(4, 8)["hr"]["mean"] == 80
    assert s["rr"]["rmssd"] is None and s["respiration"]["count"] == 0
    assert len(m.ecg_window(2, 3)) == 131 and fingerprints(path) == before


def test_corrupt_chunk_preserved_valid_streams_accessible(tmp_path):
    path = make_session(tmp_path)
    p = path / "raw/ecg/00000000.json"
    p.chmod(0o644)
    p.write_text("broken")
    m = SessionAnalysis(path)
    assert (
        m.issues
        and m.summary()["hr"]["mean"] == 80
        and m.summary()["ecg_coverage"] == 0
    )
    assert p.read_text() == "broken"


def test_active_session_refused(tmp_path):
    w = SessionWriter(tmp_path, "active")
    with pytest.raises(ValueError, match="Stop or recover"):
        SessionAnalysis(w.path)


def test_respiration_pointer_corruption_reported(tmp_path):
    p = make_session(tmp_path)
    atomic_json(p / "derived/respiration/latest.json", {"run": "../../elsewhere"})
    m = SessionAnalysis(p)
    assert not m.resp and any("Invalid respiration" in e["error"] for e in m.issues)


def test_empty_session_and_empty_range_graceful(tmp_path):
    w = SessionWriter(tmp_path, "empty")
    w.finish()
    m = SessionAnalysis(w.path)
    assert m.summary()["hr"]["mean"] is None
    with pytest.raises(ValueError):
        m.summary(1, 0)


def test_saved_respiration_rejection_kept_as_gap(tmp_path):
    p = make_session(tmp_path)
    base = p / "derived/respiration/run_test"
    base.mkdir()
    atomic_json(p / "derived/respiration/latest.json", {"run": "run_test"})
    atomic_json(base / "metadata.json", {"algorithm": "test"})
    for n, (status, value) in enumerate(
        [("ok", 12), ("low_confidence", 9), ("ok", 14)]
    ):
        atomic_json(
            base / f"{n:06d}.json",
            {"end_s": n * 3, "rate_bpm": value, "status": status, "confidence": 0.7},
        )
    m = SessionAnalysis(p)
    assert m.summary()["respiration"]["mean"] == 13 and m.resp[1]["value"] is None
