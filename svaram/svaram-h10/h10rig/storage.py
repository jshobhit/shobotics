"""Append-only raw chunks, atomic manifests, independent verification and exports."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .labels import LABEL_SCHEMA_VERSION, SOUND_VALUES

VERSION = "0.2.0"
STREAMS = {
    "ecg": {"sample_rate_hz": 130, "unit": "uV"},
    "accelerometer": {"sample_rate_hz": 50, "unit": "mg"},
    "hr": {"unit": "bpm"},
    "rr": {"unit": "1/1024 s"},
    "packets": {"unit": "original BLE bytes, hexadecimal"},
}


def utc():
    """Return a timezone-aware UTC timestamp for persisted metadata."""
    return datetime.now(timezone.utc).isoformat()


def canonical(obj):
    """Serialize deterministic JSON bytes for storage and checksum calculation."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sync_dir(path):
    """Persist directory changes as well as the contents of the files."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, obj, exclusive=False):
    """Verify bytes before publication; never overwrite a completed raw chunk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(obj)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if temp.read_bytes() != data:
        raise OSError("Read-back verification failed: " + str(temp))
    if exclusive:
        # link is atomic and refuses to replace an existing file.
        os.link(temp, path)
        temp.unlink()
        path.chmod(0o444)
    else:
        os.replace(temp, path)
    sync_dir(path.parent)


def read_chunk(path):
    """Validate the checksum and sample count before returning a raw chunk."""
    obj = json.loads(Path(path).read_text())
    digest = obj.pop("checksum")
    if hashlib.sha256(canonical(obj)).hexdigest() != digest:
        raise ValueError("Checksum mismatch")
    if obj["sample_count"] != len(obj["payload"]):
        raise ValueError("Sample count mismatch")
    obj["checksum"] = digest
    return obj


class SessionWriter:
    """Owned by the acquisition thread. At most five seconds per stream in memory."""

    def __init__(
        self, root, device, participant="", simulated=False, clock=time.monotonic,
        study=None,
    ):
        self.clock = clock
        self.started_mono = clock()
        sid = study["recording_id"] if study else datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        self.path = Path(root) / sid
        self.path.mkdir(parents=True, exist_ok=False)
        self.buffers = defaultdict(list)
        self.last_flush = self.started_mono
        self.closed = False
        self.sound_label = None
        self.label_sequence = 0
        self.last_label_time = 0.0
        self.manifest = dict(
            schema_version=1,
            sound_labels={"schema_version": LABEL_SCHEMA_VERSION,
                          "path": "events/sound_labels.jsonl",
                          "values": list(SOUND_VALUES), "count": 0},
            session_id=sid,
            device="Polar H10",
            device_id=device,
            participant_code=participant,
            simulated=simulated,
            software_version=VERSION,
            started_at=utc(),
            ended_at=None,
            status="recording",
            clock={
                "host_monotonic_start_s": self.started_mono,
                "host_utc_start": utc(),
                "method": "shared sensor end-of-packet anchor per connection; HR/RR host receipt",
                "sensor_timestamp_unit": "ns since 2000-01-01, retained as integer",
                "hr_rr_timing": "RR beat endpoints reconstructed backwards from receipt; BLE latency unknown",
            },
            configuration={
                "chunk_duration_s": 5,
                "ecg_hz": 130,
                "acc_hz": 50,
                "acc_resolution_bits": 16,
                "acc_range_g": 8,
            },
            streams={k: dict(v, chunks=0, samples=0) for k, v in STREAMS.items()},
            files=[],
            dropouts=[],
            sequence_errors=[],
            integrity_errors=[],
        )
        if study:
            self.manifest.update(
                recording_id=sid,
                study_session_id=study["study_session_id"],
                participant_id=study["participant_id"],
            )
        for stream in STREAMS:
            (self.path / "raw" / stream).mkdir(parents=True)
        (self.path / "events").mkdir()
        (self.path / "derived" / "respiration").mkdir(parents=True)
        atomic_json(
            self.path / "active.json", {"pid": os.getpid(), "created_at": utc()}
        )
        self.save_manifest()
        self.set_sound_label("silence", 0)
        self.event("connection", "recording_started", 0, device_id=device)

    def save_manifest(self):
        """Atomically publish the current session index and recording status."""
        atomic_json(self.path / "manifest.json", self.manifest)

    def event(self, category, kind, t, **detail):
        """Append and sync an event; also index connection dropouts in the manifest."""
        with (self.path / "events" / f"{category}.jsonl").open("ab") as f:
            f.write(
                canonical(dict(type=kind, t=round(t, 9), utc=utc(), **detail)) + b"\n"
            )
            f.flush()
            os.fsync(f.fileno())
        if category == "connection" and kind in ("disconnected", "reconnected"):
            self.manifest["dropouts"].append(dict(type=kind, t=t, **detail))
            self.save_manifest()

    def add(self, stream, rows):
        """Buffer nonnegative session-time observations until the next flush."""
        if self.closed:
            raise RuntimeError("Session is closed")
        self.buffers[stream].extend(r for r in rows if r["t"] >= 0)
        # A runaway producer must not grow memory indefinitely if the clock stalls.
        if len(self.buffers[stream]) > 20000:
            raise RuntimeError("Acquisition buffer limit exceeded")

    def set_sound_label(self, value, t):
        """Durably append a state transition; publish state only after fsync.

        A state applies from its timestamp (inclusive) to the next transition
        (exclusive), or the end of the recording. Raw samples stay untouched.
        """
        import math

        if self.closed:
            raise RuntimeError("Session is closed")
        if value not in SOUND_VALUES:
            raise ValueError("Unknown sound label")
        if not math.isfinite(t) or t < self.last_label_time:
            raise ValueError("Sound label time must be finite and nondecreasing")
        if value == self.sound_label:
            return value
        body = dict(
            schema_version=LABEL_SCHEMA_VERSION,
            session_id=self.manifest["session_id"],
            id=str(uuid.uuid4()), sequence=self.label_sequence,
            type="sound_state", t=round(t, 9), utc=utc(),
            label={"value": value},
        )
        body["checksum"] = hashlib.sha256(canonical(body)).hexdigest()
        with (self.path / "events" / "sound_labels.jsonl").open("ab") as f:
            f.write(canonical(body) + b"\n")
            f.flush()
            os.fsync(f.fileno())
        if self.label_sequence == 0:
            sync_dir(self.path / "events")
        self.sound_label = value
        self.last_label_time = t
        self.label_sequence += 1
        self.manifest["sound_labels"]["count"] = self.label_sequence
        return value

    def flush(self, force=False):
        """Commit buffered streams every five seconds, or immediately if forced.

        Return only the ECG rows saved by this flush so derived respiration
        uses committed data rather than samples still held in memory.
        """
        now = self.clock()
        if not force and now - self.last_flush < 5:
            return []
        committed_ecg = []
        for stream, rows in self.buffers.items():
            if not rows:
                continue
            info = self.manifest["streams"][stream]
            seq = info["chunks"]
            body = dict(
                schema_version=1,
                session_id=self.manifest["session_id"],
                signal=stream,
                sequence=seq,
                start_timestamp=rows[0]["t"],
                end_timestamp=rows[-1]["t"],
                sample_count=len(rows),
                sampling=STREAMS[stream],
                payload=rows,
            )
            body["checksum"] = hashlib.sha256(canonical(body)).hexdigest()
            rel = f"raw/{stream}/{seq:08d}.json"
            atomic_json(self.path / rel, body, exclusive=True)

            # Publish the raw chunk first; recovery can find it if the manifest write fails.
            self.manifest["files"].append(
                dict(
                    path=rel,
                    signal=stream,
                    sequence=seq,
                    checksum=body["checksum"],
                    sample_count=len(rows),
                )
            )
            info["chunks"] += 1
            info["samples"] += len(rows)
            if stream == "ecg":
                committed_ecg.extend(rows)
            self.buffers[stream] = []
        self.manifest["last_committed_at"] = utc()
        self.manifest["duration_s"] = now - self.started_mono
        self.save_manifest()
        self.last_flush = now
        return committed_ecg

    def finish(self):
        """Save the final buffers before marking the session complete."""
        self.flush(True)
        self.event("connection", "recording_stopped", self.clock() - self.started_mono)
        self.manifest.update(
            status="complete",
            ended_at=utc(),
            duration_s=self.clock() - self.started_mono,
        )
        self.save_manifest()
        (self.path / "active.json").unlink()
        sync_dir(self.path)
        self.closed = True
        return self.path


def verify_session(path, persist=True):
    """Check raw files against their checksums, identities, and manifest.

    Report missing, corrupt, duplicate, and unindexed chunks independently.
    With persist=False, leave the session files untouched.
    """
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    errors, valid = [], []
    expected = {x["path"]: x for x in manifest.get("files", [])}
    found = set()
    seen = defaultdict(set)
    for p in sorted((path / "raw").glob("*/*.json")):
        rel = p.relative_to(path).as_posix()
        found.add(rel)
        try:
            obj = read_chunk(p)
            stream, seq = obj["signal"], obj["sequence"]
            if stream not in STREAMS or stream != p.parent.name:
                raise ValueError("Stream identity mismatch")
            if obj["session_id"] != manifest["session_id"]:
                raise ValueError("Session identity mismatch")
            if seq in seen[stream]:
                errors.append(
                    {"file": rel, "error": "duplicate sequence", "sequence": seq}
                )
            seen[stream].add(seq)
            if rel in expected and expected[rel]["checksum"] != obj["checksum"]:
                raise ValueError("Manifest checksum mismatch")
            valid.append(
                dict(
                    path=rel,
                    signal=stream,
                    sequence=seq,
                    checksum=obj["checksum"],
                    sample_count=obj["sample_count"],
                )
            )
            if rel not in expected:
                errors.append(
                    {
                        "file": rel,
                        "error": "committed chunk absent from manifest (recoverable)",
                    }
                )
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors.append({"file": rel, "error": str(exc)})
    for rel in sorted(set(expected) - found):
        errors.append({"file": rel, "error": "missing chunk"})
    for stream, info in manifest.get("streams", {}).items():
        seqs = seen[stream]
        count = max(info.get("chunks", 0), max(seqs, default=-1) + 1)
        for seq in range(count):
            if seq not in seqs:
                errors.append(
                    {
                        "signal": stream,
                        "sequence": seq,
                        "error": "missing or corrupt sequence",
                    }
                )
    for temp in sorted(path.rglob("*.tmp")):
        errors.append(
            {
                "file": temp.relative_to(path).as_posix(),
                "error": "unfinished temporary file (preserved)",
            }
        )
    report = dict(
        verified_at=utc(), valid_chunks=len(valid), errors=errors, files=valid
    )
    if persist:
        manifest["integrity_errors"] = errors
        manifest["sequence_errors"] = [
            e
            for e in errors
            if any(s in e["error"] for s in ("sequence", "missing", "duplicate"))
        ]
        atomic_json(path / "manifest.json", manifest)
        atomic_json(path / "verification.json", report)
    return report


def recover_session(path):
    """Rebuild an incomplete session index from surviving committed chunks."""
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    if (
        manifest["status"] not in ("recording", "incomplete")
        and not (path / "active.json").exists()
    ):
        raise ValueError("Only an incomplete session can be recovered")
    report = verify_session(path)
    manifest = json.loads((path / "manifest.json").read_text())
    # Keep original expectations, including missing and corrupt files, for future audits.
    by_path = {x["path"]: x for x in manifest.get("files", [])}
    by_path.update({x["path"]: x for x in report["files"]})
    manifest["files"] = list(by_path.values())
    for stream, info in manifest["streams"].items():
        entries = [e for e in by_path.values() if e["signal"] == stream]
        info["chunks"] = max(
            info.get("chunks", 0), max((e["sequence"] + 1 for e in entries), default=0)
        )
        info["samples"] = sum(e["sample_count"] for e in entries)
        info["valid_chunks_at_recovery"] = sum(
            e["signal"] == stream for e in report["files"]
        )
    manifest.update(
        status="recovered",
        recovered_at=utc(),
        ended_at=None,
        recovery_note="Original stop time unknown; uncommitted memory may be lost.",
    )
    end = 0
    for entry in report["files"]:
        end = max(end, read_chunk(path / entry["path"])["end_timestamp"])
    manifest["duration_s"] = max(manifest.get("duration_s", 0), end)
    atomic_json(path / "manifest.json", manifest)
    (path / "active.json").unlink(missing_ok=True)
    sync_dir(path)
    return verify_session(path)


def iter_rows(path, stream):
    """Yield valid independent chunks even if another chunk is corrupt."""
    seen = set()
    manifest = json.loads((Path(path) / "manifest.json").read_text())
    expected = {x["path"]: x["checksum"] for x in manifest.get("files", [])}
    for p in sorted((Path(path) / "raw" / stream).glob("*.json")):
        try:
            obj = read_chunk(p)
            rel = p.relative_to(path).as_posix()
            if obj["signal"] != stream or obj["session_id"] != manifest["session_id"]:
                continue
            if rel in expected and obj["checksum"] != expected[rel]:
                continue
            if obj["sequence"] in seen:
                continue
            seen.add(obj["sequence"])
        except (ValueError, KeyError, TypeError, OSError):
            continue
        yield from obj["payload"]


def export_session(path, destination):
    """Export a finished session and valid-row CSVs through a temporary ZIP."""
    path, destination = Path(path), Path(destination)
    manifest = json.loads((path / "manifest.json").read_text())
    if (path / "active.json").exists() or manifest["status"] not in (
        "complete",
        "recovered",
    ):
        raise ValueError("Stop or recover the session before exporting")
    if destination.resolve().is_relative_to(path.resolve()):
        raise ValueError("Choose an export location outside the session folder")
    report = verify_session(path)
    temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(path.rglob("*")):
            if p.is_file() and not p.is_symlink():
                z.write(p, Path(path.name) / p.relative_to(path))

        for stream in STREAMS:
            # Spool CSV to disk rather than loading an hour-long recording into RAM.
            import tempfile

            with tempfile.TemporaryFile(mode="w+", newline="") as f:
                writer = None
                for row in iter_rows(path, stream):
                    if writer is None:
                        writer = csv.DictWriter(
                            f, fieldnames=list(row), extrasaction="ignore"
                        )
                        writer.writeheader()
                    writer.writerow(row)
                f.seek(0)
                with z.open(path.name + "/csv/" + stream + ".csv", "w") as target:
                    while block := f.read(65536):
                        target.write(block.encode())
    with temp.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(temp, destination)
    sync_dir(destination.parent)
    return report
