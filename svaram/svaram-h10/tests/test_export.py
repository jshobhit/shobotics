import csv
import io
import json
import zipfile

from h10rig.config import SILENCE
from h10rig.documents import Document
from h10rig.export import beats_csv, ecg_csv, export_bytes, file_name, session_json
from h10rig.session import Session


def doc(name, data=b"x"):
    return Document(name, len(data), "", "0" * 64, "2026-10-10T00:00:00.000Z", data)


def stopped_session():
    s = Session()
    docs = {
        "consent": [doc("consent.pdf")],
        "questionnaire": [doc("q.csv"), doc("q.csv")],
    }
    s.start({"p": "PT-025", "s": "S-2026-019", "f": "FC-03"}, docs, 0.0)
    t = 0.5
    while t < 40:
        s.add_beat(t, 1000)
        t += 1
    s.toggle("Plate Gong", 40)
    s.toggle("Ocean Drum", 41)
    s.add_beat(40.5, 900)
    s.add_beat(41.5, 900)
    s.add_ecg(41.0, [10, -20, 30])
    for i in range(42):
        s.sample_hrv(i, True)
    s.stop(42)
    return s


def test_beats_csv_columns_and_labels():
    rows = list(csv.reader(io.StringIO(beats_csv(stopped_session()))))
    assert rows[0] == [
        "t_s", "wallclock", "rr_ms", "hr_bpm", "artifact", "labels",
        "participant_id", "session_id", "facilitator_id",
    ]  # fmt: skip
    assert rows[1][2:] == [
        "1000",
        "60.0",
        "0",
        SILENCE,
        "PT-025",
        "S-2026-019",
        "FC-03",
    ]
    assert rows[-1][5] == "Ocean Drum|Plate Gong"
    assert rows[1][1].endswith("Z")


def test_ecg_csv_tags_samples_with_labels():
    lines = ecg_csv(stopped_session()).splitlines()
    assert lines[0] == "t_s,ecg_uv,labels"
    assert lines[1].split(",")[1:] == ["10", "Plate Gong"]
    assert lines[3].split(",")[1:] == ["30", "Ocean Drum|Plate Gong"]  # on at 41.0


def test_session_json_shape():
    d = json.loads(session_json(stopped_session()))
    assert d["format"] == "sonorium-session/4"
    assert d["duration_s"] == 42
    assert d["reference"]["hr_bpm"] == 60
    assert set(d["rolling_rmssd"]) == {"15s", "30s", "45s"}
    assert [i["label"] for i in d["label_intervals"]] == [
        SILENCE,
        "Plate Gong",
        "Ocean Drum",
    ]
    assert d["documents"]["consent"][0]["sha256"] == "0" * 64


def test_session_package_opens_and_holds_everything():
    s = stopped_session()
    z = zipfile.ZipFile(io.BytesIO(export_bytes(s, "zip")))
    assert z.testzip() is None
    folder = "S-2026-019_PT-025/"
    assert sorted(z.namelist()) == sorted(
        folder + n
        for n in (
            "beats.csv", "ecg.csv", "session.json", "consent/consent.pdf",
            "questionnaire/q.csv", "questionnaire/q (2).csv",
        )
    )  # fmt: skip
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
    assert file_name(s, "zip") == "S-2026-019_PT-025.zip"
