"""Session exports: beats CSV, ECG CSV, session JSON and the session package.

All functions read a stopped `Session` and return text or bytes; the window
decides where to save them. File names follow `<session>_<participant>...`.
"""

from __future__ import annotations

import csv
from datetime import timedelta
import io
import json
import zipfile

from .config import HRV_WINDOWS, SILENCE
from .session import Session, labels_at

FORMAT = "sonorium-session/4"

SUFFIX = {"csv": "_beats.csv", "ecg": "_ecg.csv", "json": ".json", "zip": ".zip"}


def base_name(s: Session) -> str:
    return (s.ids["s"] or "session") + "_" + (s.ids["p"] or "participant")


def file_name(s: Session, kind: str) -> str:
    return base_name(s) + SUFFIX[kind]


def label_text(labels) -> str:
    """Labels joined with "|"; an empty set is recorded as Silence."""
    return "|".join(labels) if labels else SILENCE


def iso(dt) -> str:
    """UTC timestamp like JavaScript's toISOString: 2026-10-10T12:00:00.000Z."""
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def r1(v):
    return None if v is None else round(v, 1)


def beats_csv(s: Session) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(
        ["t_s", "wallclock", "rr_ms", "hr_bpm", "artifact", "labels"]
        + ["participant_id", "session_id", "facilitator_id"]
    )
    for b in s.beats:
        if not b.rec:
            continue
        rel = s.rel(b.t)
        w.writerow(
            [
                f"{rel:.3f}",
                iso(s.started_at + timedelta(seconds=rel)),
                b.rr,
                f"{b.hr:.1f}",
                int(b.art),
                label_text(b.labels),
                s.ids["p"],
                s.ids["s"],
                s.ids["f"],
            ]
        )
    return out.getvalue()


def ecg_csv(s: Session) -> str:
    """One row per recorded sample, tagged with the labels active at that time."""
    rows = ["t_s,ecg_uv,labels"]
    times = (t for t, _ in s.ecg_rec)
    t0 = s.t0
    for (t, v), labels in zip(s.ecg_rec, labels_at(s.events, times)):
        text = label_text(labels)
        if "," in text or '"' in text:
            text = '"' + text.replace('"', '""') + '"'
        rows.append(f"{t - t0:.4f},{v},{text}")
    return "\n".join(rows) + "\n"


def session_json(s: Session) -> str:
    ref = s.reference(s.t_end)
    stats = s.segment_stats(s.t_end)
    doc = {
        "format": FORMAT,
        "sensor": "Polar H10",
        "participant_id": s.ids["p"],
        "session_id": s.ids["s"],
        "facilitator_id": s.ids["f"],
        "started_at": iso(s.started_at),
        "duration_s": r1(s.rel(s.t_end)),
        "documents": {k: [d.meta() for d in s.docs[k]] for k in s.docs},
        "reference": (
            {
                "label": "opening silence",
                "hr_bpm": r1(ref["hr"]),
                "rmssd_ms": r1(ref["rmssd"]),
                "beats": ref["beats"],
            }
            if ref and ref["ok"]
            else None
        ),
        "label_intervals": [
            {
                "label": x["label"],
                "start_s": r1(s.rel(x["t1"])),
                "end_s": r1(s.rel(x["t2"])),
                "mean_hr_bpm": r1(x["hr"]),
                "rmssd_ms": r1(x["rmssd"]),
                "hr_change_pct": r1(x["d_hr"]),
                "rmssd_change_pct": r1(x["d_rmssd"]),
            }
            for x in stats
        ],
        "rolling_rmssd": {
            f"{w}s": [
                [round(s.rel(t), 1), r1(v)] for t, v in s.hrv[w] if s.t0 <= t <= s.t_end
            ]
            for w in HRV_WINDOWS
        },
        "events": [
            {"t_s": round(s.rel(e["t"]), 2), "kind": e["kind"], "text": e["text"]}
            for e in s.events
        ],
        "beats": [
            {
                "t_s": round(s.rel(b.t), 3),
                "rr_ms": b.rr,
                "artifact": b.art,
                "labels": list(b.labels) if b.labels is not None else None,
            }
            for b in s.beats
            if b.rec
        ],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)


def session_zip(s: Session) -> bytes:
    """Store-only zip: beats.csv, ecg.csv, session.json and attached documents.

    Uses the standard library's zipfile with ZIP_STORED (no compression), which
    writes the same layout as the reference writer: CRC-32, local headers, a
    central directory and the UTF-8 name flag for non-ASCII names.
    """
    folder = base_name(s) + "/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr(folder + "beats.csv", beats_csv(s))
        z.writestr(folder + "ecg.csv", ecg_csv(s))
        z.writestr(folder + "session.json", session_json(s))
        for kind in ("consent", "questionnaire"):
            used = set()
            for d in s.docs[kind]:
                name = _unique(d.name, used)
                z.writestr(f"{folder}{kind}/{name}", d.data)
    return buf.getvalue()


def export_bytes(s: Session, kind: str) -> bytes:
    if kind == "zip":
        return session_zip(s)
    text = {"csv": beats_csv, "ecg": ecg_csv, "json": session_json}[kind](s)
    return text.encode("utf-8")


def _unique(name: str, used: set) -> str:
    """Avoid duplicate entries when two attached files share a name."""
    stem, dot, ext = name.rpartition(".")
    candidate, n = name, 2
    while candidate in used:
        candidate = f"{stem} ({n}).{ext}" if dot else f"{name} ({n})"
        n += 1
    used.add(candidate)
    return candidate
