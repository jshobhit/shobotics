"""Study identity, survey durability and recorder recovery acceptance tests."""

import json
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

import pytest

from h10rig.registry import Registry, Conflict
from h10rig.storage import SessionWriter, recover_session, verify_session


@pytest.fixture
def registry(tmp_path):
    registry = Registry(tmp_path / "data", create=True)
    registry.register_questionnaire("checkin", "1", {"state": "0 to 10"})
    return registry


def ready(registry, code="P-001"):
    pid = registry.add_participant(code)
    sid = registry.create_session(pid)
    save(registry, sid)
    return pid, sid


def save(registry, sid, kind="pre", version=0, submit=True, data=None, request_id=None):
    return registry.save_assessment(sid, kind, "checkin", "1", data or {
        "answers": {"state_calm": 0, "state_stress": None}, "answer_times": {}
    }, version, submit, request_id or str(uuid.uuid4()))


def writer(registry, sid, simulated=False):
    study = registry.reserve_recording(sid, "test-device", simulated)
    return SessionWriter(study["root"], "test-device", study["participant_code"], simulated=simulated, study=study)


def test_distinct_visits_restarts_and_moved_root(registry, tmp_path):
    pid, sid = ready(registry)
    second = registry.create_session(pid)
    assert [s["visit"] for s in registry.sessions()] == [2, 1]
    assert registry.session(second)["status"] == "draft"
    first = writer(registry, sid)
    first.add("ecg", [{"t": 0, "uv": 42}])
    first.finish()
    raw = next((first.path / "raw/ecg").glob("*.json"))
    original = raw.read_bytes()
    registry.sync_recording(first.path)
    another = writer(registry, sid)
    another.finish()
    registry.sync_recording(another.path)
    save(registry, sid, "post")
    assert registry.session(sid)["status"] == "complete"
    assert registry.session(second)["status"] == "draft"
    assert raw.read_bytes() == original
    assert first.manifest["recording_id"] != another.manifest["recording_id"]
    moved = tmp_path / "moved"
    shutil.copytree(registry.root, moved)
    reopened = Registry(moved)
    assert reopened.reconcile() == []
    assert reopened.sessions()[1]["recording_count"] == 2
    assert reopened.assessment(sid, "pre")["data"]["answers"] == {"state_calm": 0, "state_stress": None}


def test_immutable_submission_idempotency_and_stale_drafts(registry):
    pid = registry.add_participant("P-001")
    sid = registry.create_session(pid)
    rid = str(uuid.uuid4())
    first = save(registry, sid, request_id=rid)
    assert save(registry, sid, request_id=rid) == first
    with pytest.raises(Conflict, match="another tab"):
        save(registry, sid, submit=False)
    changed = {"answers": {"state_calm": 5}, "answer_times": {"state_calm": "2026-10-06T00:00:00Z"}}
    second = save(registry, sid, version=first["version"], data=changed)
    assert second["revision"] == 2
    with registry.connection() as db:
        bodies = [json.loads(r[0]) for r in db.execute("SELECT data FROM assessments ORDER BY revision")]
    assert bodies[0]["answers"]["state_calm"] == 0
    assert bodies[1]["answers"]["state_calm"] == 5
    with pytest.raises(Conflict):
        save(registry, sid, version=2, data=changed, request_id=rid)


def test_lock_failure_does_not_report_saved(registry):
    _, sid = ready(registry)
    with registry.connection() as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        before = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            save(registry, sid, version=1)
        assert time.monotonic() - before < 2
    assert registry.assessment(sid, "pre")["revision"] == 1


def test_recovery_reconciles_manifest_and_phase_without_rewriting_raw(registry):
    _, sid = ready(registry)
    recording = writer(registry, sid)
    recording.add("ecg", [{"t": 0, "uv": 8}, {"t": 1 / 130, "uv": 9}])
    recording.flush(True)
    recording.event("study", "phase", 0, id=str(uuid.uuid4()), phase="rest")
    raw = next((recording.path / "raw/ecg").glob("*.json"))
    before = raw.read_bytes()
    assert registry.reconcile() == []
    with pytest.raises(Conflict, match="active recording"):
        writer(registry, sid)
    recover_session(recording.path)
    assert registry.reconcile() == []
    assert registry.reconcile() == []
    assert raw.read_bytes() == before
    save(registry, sid, "post")
    session = registry.session(sid)
    assert session["status"] == "complete" and session["needs_review"] == 1
    with registry.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM phase_markers").fetchone()[0] == 1


def test_simulation_does_not_complete_real_visit(registry):
    _, sid = ready(registry)
    recording = writer(registry, sid, simulated=True)
    recording.finish()
    registry.sync_recording(recording.path)
    save(registry, sid, "post")
    assert registry.session(sid)["status"] == "in_progress"


def test_future_schema_rejected_before_write_and_backup_is_valid(registry, tmp_path):
    _, sid = ready(registry)
    backup = tmp_path / "backup.sqlite"
    registry.backup(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT id FROM sessions").fetchone()[0] == sid
    with registry.connection() as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        Registry(registry.root)
    with pytest.raises(ValueError, match="Unsupported"):
        registry.add_participant("P-002")


def test_legacy_recording_remains_unlinked(registry):
    recording = SessionWriter(registry.root / "sessions", "legacy", "P-001")
    recording.finish()
    before = (recording.path / "manifest.json").read_bytes()
    registry.sync_recording(recording.path)
    assert verify_session(recording.path, persist=False)["errors"] == []
    assert (recording.path / "manifest.json").read_bytes() == before
    with registry.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 0


def test_identity_and_definition_constraints(registry):
    _, sid = ready(registry)
    with pytest.raises(sqlite3.IntegrityError):
        registry.add_participant("p-001")
    with pytest.raises(sqlite3.IntegrityError):
        registry.create_session("missing")
    with pytest.raises(Conflict, match="version"):
        registry.register_questionnaire("checkin", "1", {"changed": True})
    recording = writer(registry, sid)
    recording.finish()
    m = recording.manifest.copy()
    m["participant_id"] = "wrong"
    (recording.path / "manifest.json").write_text(json.dumps(m))
    assert "participant" in registry.reconcile()[0]


def test_disk_failure_does_not_reserve_recording(registry, monkeypatch):
    _, sid = ready(registry)
    real_open = Path.open

    def fail_probe(path, *args, **kwargs):
        if path.name.startswith(".write-test"):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_probe)
    with pytest.raises(OSError, match="disk full"):
        writer(registry, sid)
    assert registry.sessions()[0]["recording_count"] == 0
