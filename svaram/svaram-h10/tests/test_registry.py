"""Study registry acceptance tests (the registry serves the survey app in svaram-data)."""

import json
import sqlite3
import time
import uuid

import pytest

from h10rig.registry import Registry, Conflict


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


