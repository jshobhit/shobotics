"""Shared local study registry. No Qt, Bluetooth or third-party dependencies."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

SCHEMA_VERSION = 1


def utc():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Conflict(ValueError):
    """A stale draft or incompatible transition must not overwrite data."""


class Registry:
    def __init__(self, root, create=False):
        self.root = Path(root).resolve()
        self.path = self.root / "study.sqlite"
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        elif not self.path.is_file():
            raise ValueError("No study registry here. Start the survey app first.")
        with self.connection(create=create) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in ((0, SCHEMA_VERSION) if create else (SCHEMA_VERSION,)):
                raise ValueError(f"Unsupported registry schema: {version}")
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError("Refusing to initialise an unknown database")
                db.executescript(SCHEMA)
            db.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connection(self, create=False):
        uri = self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
        db = sqlite3.connect(uri, uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if not create and db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("Unsupported registry schema; no changes were saved")
            with db:
                yield db
        finally:
            db.close()

    def add_participant(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", code):
            raise ValueError("Use a study code with letters, numbers, hyphens or underscores")
        pid = str(uuid.uuid4())
        with self.connection() as db:
            db.execute("INSERT INTO participants VALUES (?,?,?)", (pid, code, utc()))
        return pid

    def participants(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM participants ORDER BY code")]

    def create_session(self, participant_id, protocol="sonorium-2026-v1"):
        if not isinstance(protocol, str) or not 1 <= len(protocol.strip()) <= 100:
            raise ValueError("A protocol version is required")
        sid = str(uuid.uuid4())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            visit = db.execute("SELECT COALESCE(MAX(visit),0)+1 FROM sessions WHERE participant_id=?", (participant_id,)).fetchone()[0]
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (sid, participant_id, visit, protocol, "draft", utc(), 0))
        return sid

    def sessions(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("""
                SELECT s.*, p.code,
                EXISTS(SELECT 1 FROM assessments a WHERE a.session_id=s.id AND a.kind='pre') AS pre_submitted,
                EXISTS(SELECT 1 FROM assessments a WHERE a.session_id=s.id AND a.kind='post') AS post_submitted,
                (SELECT COUNT(*) FROM recordings r WHERE r.session_id=s.id) AS recording_count,
                (SELECT status FROM recordings r WHERE r.session_id=s.id ORDER BY created_at DESC LIMIT 1) AS recording_status
                FROM sessions s JOIN participants p ON p.id=s.participant_id ORDER BY s.created_at DESC
            """)]

    def session(self, sid):
        with self.connection() as db:
            row = db.execute("SELECT s.*,p.code FROM sessions s JOIN participants p ON p.id=s.participant_id WHERE s.id=?", (sid,)).fetchone()
            if row is None:
                raise ValueError("Study session not found")
            return dict(row)

    def register_questionnaire(self, qid, version, definition):
        value = encode(definition)
        with self.connection() as db:
            existing = db.execute("SELECT definition FROM questionnaires WHERE id=? AND version=?", (qid, version)).fetchone()
            if existing and existing[0] != value:
                raise Conflict("Questionnaire changed without a version change")
            db.execute("INSERT OR IGNORE INTO questionnaires VALUES (?,?,?)", (qid, version, value))

    @staticmethod
    def _kind(kind):
        if kind not in ("pre", "post"):
            raise ValueError("Unknown assessment type")

    def assessment(self, sid, kind):
        self._kind(kind)
        self.session(sid)
        with self.connection() as db:
            draft = db.execute("SELECT * FROM drafts WHERE session_id=? AND kind=?", (sid, kind)).fetchone()
            latest = db.execute("SELECT * FROM assessments WHERE session_id=? AND kind=? ORDER BY revision DESC LIMIT 1", (sid, kind)).fetchone()
            row = draft or latest
            return dict(
                data=json.loads(row["data"]) if row else None,
                version=draft["version"] if draft else 0,
                revision=latest["revision"] if latest else 0,
                submitted=bool(latest),
            )

    def save_assessment(self, sid, kind, qid, qversion, data, expected_version, submit=False, request_id=None):
        self._kind(kind)
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict) or not isinstance(data.get("answer_times"), dict):
            raise ValueError("Answers and answer times must be objects")
        if not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("Invalid draft version")
        if submit and (not isinstance(request_id, str) or len(request_id) > 80 or not request_id):
            raise ValueError("Submission request ID required")
        body = encode(data)
        if len(body.encode()) > 200_000:
            raise ValueError("Assessment exceeds the size limit")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()
            if not session or session[0] == "cancelled":
                raise ValueError("Session unavailable")
            if submit:
                prior = db.execute("SELECT * FROM assessments WHERE request_id=?", (request_id,)).fetchone()
                if prior:
                    if prior["session_id"] != sid or prior["kind"] != kind or prior["data"] != body:
                        raise Conflict("Submission ID already used for different answers")
                    return {"version": prior["draft_version"], "revision": prior["revision"], "submitted": True}
            draft = db.execute("SELECT version FROM drafts WHERE session_id=? AND kind=?", (sid, kind)).fetchone()
            current = draft[0] if draft else 0
            if current != expected_version:
                raise Conflict("This form changed in another tab. Reload before editing.")
            version = current + 1
            db.execute("""INSERT INTO drafts VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(session_id,kind) DO UPDATE SET questionnaire_id=excluded.questionnaire_id,
                questionnaire_version=excluded.questionnaire_version,data=excluded.data,version=excluded.version,updated_at=excluded.updated_at
                """, (sid, kind, qid, qversion, body, version, utc()))
            revision = 0
            if submit:
                revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM assessments WHERE session_id=? AND kind=?", (sid, kind)).fetchone()[0]
                db.execute("INSERT INTO assessments VALUES (?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), sid, kind, qid, qversion, revision, body, utc(), request_id, version))
                self._update_status(db, sid)
            return {"version": version, "revision": revision, "submitted": submit}

    @staticmethod
    def _update_status(db, sid):
        if db.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0] == "cancelled":
            return
        types = {r[0] for r in db.execute("SELECT kind FROM assessments WHERE session_id=?", (sid,))}
        recordings = list(db.execute("SELECT status,simulated FROM recordings WHERE session_id=?", (sid,)))
        active = any(r[0] in ("starting", "recording") for r in recordings)
        saved = any(r[0] in ("complete", "recovered") and not r[1] for r in recordings)
        review = any(r[0] == "recovered" for r in recordings)
        status = "complete" if {"pre", "post"} <= types and saved and not active else "in_progress" if recordings else "ready" if "pre" in types else "draft"
        db.execute("UPDATE sessions SET status=?,needs_review=? WHERE id=?", (status, int(review), sid))

    def reserve_recording(self, sid, device, simulated=False):
        rid = str(uuid.uuid4())
        relative = f"recordings/{sid}/{rid}"
        # Check the filesystem before reserving a registry row.
        self.session(sid)
        folder = self.root / "recordings" / sid
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / (".write-test-" + rid)
        with probe.open("xb") as handle:
            handle.write(b"storage probe")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute("SELECT s.*,p.code FROM sessions s JOIN participants p ON p.id=s.participant_id WHERE s.id=?", (sid,)).fetchone()
            if session["status"] not in ("ready", "in_progress"):
                raise Conflict("Select a ready or unfinished study session")
            if db.execute("SELECT 1 FROM recordings WHERE session_id=? AND status IN ('starting','recording')", (sid,)).fetchone():
                raise Conflict("This visit already has an active recording; stop or recover it first")
            db.execute("INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?)", (rid, sid, device, relative, "starting", int(simulated), utc(), None, None, os.getpid()))
            self._update_status(db, sid)
        return {"recording_id": rid, "study_session_id": sid, "participant_id": session["participant_id"], "participant_code": session["code"], "root": str(folder)}

    def sync_recording(self, path):
        path = Path(path).resolve()
        relative = path.relative_to(self.root).as_posix()
        manifest = json.loads((path / "manifest.json").read_text())
        sid, rid = manifest.get("study_session_id"), manifest.get("recording_id")
        if not sid or not rid:
            return  # Old recordings stay unlinked.
        if relative != f"recordings/{sid}/{rid}" or manifest["session_id"] != rid:
            raise ValueError("Recording identity or location does not match")
        markers = []
        event_path = path / "events" / "study.jsonl"
        if event_path.exists():
            for line in event_path.read_text().splitlines():
                event = json.loads(line)
                if event.get("type") == "phase":
                    markers.append(event)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute("SELECT participant_id FROM sessions WHERE id=?", (sid,)).fetchone()
            if not session or session[0] != manifest.get("participant_id"):
                raise ValueError("Recording participant does not match its visit")
            db.execute("""INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,started_at=excluded.started_at,ended_at=excluded.ended_at""",
                (rid, sid, manifest["device_id"], relative, manifest["status"], int(manifest.get("simulated", False)), manifest["started_at"], manifest["started_at"], manifest.get("ended_at"), None))
            for event in markers:
                db.execute("INSERT OR IGNORE INTO phase_markers VALUES (?,?,?,?,?,?)", (event["id"], sid, rid, event["phase"], event["t"], event["utc"]))
            self._update_status(db, sid)

    def reconcile(self):
        """Index durable manifests and events. Never rewrite raw files or infer identities."""
        errors = []
        with self.connection() as db:
            reservations = list(db.execute("SELECT id,relative_path,owner_pid FROM recordings WHERE status='starting'"))
        for row in reservations:
            if (self.root / row["relative_path"] / "manifest.json").exists():
                continue
            try:
                if row["owner_pid"]:
                    os.kill(row["owner_pid"], 0)
                    continue
            except ProcessLookupError:
                pass
            except PermissionError:
                continue
            self.abandon_reservation(row["id"])
        for path in (self.root / "recordings").glob("*/*/manifest.json"):
            try:
                self.sync_recording(path.parent)
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append(f"{path.parent.name}: {exc}")
        return errors

    def abandon_reservation(self, rid):
        with self.connection() as db:
            row = db.execute("SELECT session_id FROM recordings WHERE id=?", (rid,)).fetchone()
            if row:
                db.execute("UPDATE recordings SET status='incomplete' WHERE id=?", (rid,))
                self._update_status(db, row[0])

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("Backup destination already exists")
        with self.connection() as source, sqlite3.connect(destination) as target:
            source.backup(target)


SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE participants(id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE, created_at TEXT NOT NULL);
CREATE TABLE sessions(id TEXT PRIMARY KEY, participant_id TEXT NOT NULL REFERENCES participants(id), visit INTEGER NOT NULL,
 protocol TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,needs_review INTEGER NOT NULL DEFAULT 0,UNIQUE(participant_id,visit));
CREATE INDEX session_status ON sessions(status,created_at);
CREATE TABLE questionnaires(id TEXT NOT NULL,version TEXT NOT NULL,definition TEXT NOT NULL,PRIMARY KEY(id,version));
CREATE TABLE drafts(session_id TEXT NOT NULL REFERENCES sessions(id),kind TEXT NOT NULL CHECK(kind IN ('pre','post')),
 questionnaire_id TEXT NOT NULL,questionnaire_version TEXT NOT NULL,data TEXT NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(session_id,kind),FOREIGN KEY(questionnaire_id,questionnaire_version) REFERENCES questionnaires(id,version));
CREATE TABLE assessments(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),kind TEXT NOT NULL CHECK(kind IN ('pre','post')),
 questionnaire_id TEXT NOT NULL,questionnaire_version TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,submitted_at TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE,draft_version INTEGER NOT NULL,UNIQUE(session_id,kind,revision),
 FOREIGN KEY(questionnaire_id,questionnaire_version) REFERENCES questionnaires(id,version));
CREATE TABLE recordings(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),device_id TEXT NOT NULL,
 relative_path TEXT NOT NULL UNIQUE,status TEXT NOT NULL,simulated INTEGER NOT NULL,created_at TEXT NOT NULL,started_at TEXT,ended_at TEXT,owner_pid INTEGER);
CREATE INDEX recording_session ON recordings(session_id,status);
CREATE TABLE phase_markers(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),recording_id TEXT NOT NULL REFERENCES recordings(id),
 phase TEXT NOT NULL,t REAL NOT NULL,utc TEXT NOT NULL);
PRAGMA user_version=1;
COMMIT;
"""
