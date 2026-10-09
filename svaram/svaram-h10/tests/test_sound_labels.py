import hashlib
import json
import zipfile

import pytest

from h10rig.analysis import SessionAnalysis
from h10rig.storage import SessionWriter, canonical, export_session, recover_session


def session(tmp_path):
    now = [100.0]
    writer = SessionWriter(tmp_path, "fixture", clock=lambda: now[0])
    writer.set_sound_label("fire", 2)
    writer.set_sound_label("fire", 3)  # No duplicate state.
    writer.set_sound_label("water", 4)
    writer.set_sound_label("silence", 6)
    now[0] = 110
    writer.finish()
    return writer.path


def test_state_intervals_boundaries_export_and_read_only(tmp_path):
    path = session(tmp_path)
    source = path / "events/sound_labels.jsonl"
    before = source.read_bytes()
    model = SessionAnalysis(path)
    assert model.sound_intervals() == [
        (0, 2, "silence"), (2, 4, "fire"), (4, 6, "water"), (6, 10, "silence")]
    assert model.sound_intervals(3, 6) == [(3, 4, "fire"), (4, 6, "water")]
    assert [r["sequence"] for r in model.sound_labels] == list(range(4))
    assert source.read_bytes() == before
    destination = tmp_path / "export.zip"
    export_session(path, destination)
    with zipfile.ZipFile(destination) as archive:
        assert archive.read(path.name + "/events/sound_labels.jsonl") == before


@pytest.mark.parametrize("damage", ["checksum", "partial", "sequence", "missing", "tail"])
def test_damaged_labels_are_not_silently_inferred(tmp_path, damage):
    path = session(tmp_path)
    source = path / "events/sound_labels.jsonl"
    rows = source.read_text().splitlines()
    if damage == "missing":
        source.unlink()
    else:
        if damage == "checksum":
            rows[1] = rows[1].replace('"fire"', '"earth"')
        elif damage == "partial":
            rows.append('{"type":')
        elif damage == "tail":
            rows.pop()
        else:
            row = json.loads(rows[1])
            row.pop("checksum")
            row["sequence"] = 12
            row["checksum"] = hashlib.sha256(canonical(row)).hexdigest()
            rows[1] = json.dumps(row)
        source.write_text("\n".join(rows) + "\n")
    model = SessionAnalysis(path)
    assert model.sound_labels == []
    assert "integrity" in model.sound_labels_status
    assert any(i.get("file") == source.name for i in model.issues)


def test_legacy_session_not_assumed_silence(tmp_path):
    path = session(tmp_path)
    (path / "events/sound_labels.jsonl").unlink()
    manifest = path / "manifest.json"
    data = json.loads(manifest.read_text())
    data.pop("sound_labels")
    manifest.write_text(json.dumps(data))
    model = SessionAnalysis(path)
    assert model.sound_labels_status == "Not labeled"
    assert model.sound_intervals() == []


def test_recovery_preserves_labels_and_clips_to_known_duration(tmp_path):
    now = [100.0]
    writer = SessionWriter(tmp_path, "fixture", clock=lambda: now[0])
    writer.set_sound_label("wind", 2)
    writer.add("hr", [{"t": 4, "bpm": 70}])
    now[0] = 105
    writer.flush(True)
    writer.set_sound_label("earth", 7)
    before = (writer.path / "events/sound_labels.jsonl").read_bytes()
    recover_session(writer.path)
    model = SessionAnalysis(writer.path)
    assert model.sound_intervals() == [(0, 2, "silence"), (2, 5, "wind")]
    assert (writer.path / "events/sound_labels.jsonl").read_bytes() == before


def test_invalid_or_closed_changes_rejected(tmp_path):
    writer = SessionWriter(tmp_path, "fixture")
    with pytest.raises(ValueError):
        writer.set_sound_label("unknown", 1)
    with pytest.raises(ValueError):
        writer.set_sound_label("fire", -1)
    writer.finish()
    with pytest.raises(RuntimeError, match="closed"):
        writer.set_sound_label("fire", 2)
