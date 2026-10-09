"""Read-only session analysis. No raw rewriting, interpolation, or clinical scoring."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .storage import verify_session, read_chunk, canonical
from .labels import LABEL_SCHEMA_VERSION, SOUND_VALUES

ANALYSIS_VERSION = "session-overview-1.0.0"


def finite(value):
    """Accept numeric observations only when they are finite."""
    return isinstance(value, (int, float)) and math.isfinite(value)


def stats(values):
    """Summarize finite values; return None for each statistic on empty input."""
    samples = np.asarray(values, dtype=float)
    samples = samples[np.isfinite(samples)]
    return {
        "count": len(samples),
        "mean": float(np.mean(samples)) if len(samples) else None,
        "median": float(np.median(samples)) if len(samples) else None,
        "min": float(np.min(samples)) if len(samples) else None,
        "max": float(np.max(samples)) if len(samples) else None,
    }


def rr_metrics(rows, start, end):
    """Adjacent ORIGINAL RR observations only; never join across rejected rows or gaps."""
    selected = [r for r in rows if start <= r["t"] <= end]
    valid = [r for r in selected if 300 <= r["ms"] <= 2000]
    pairs = []
    # Test adjacency before filtering: removing a rejected row must not connect
    # the two observations on either side of it into a fabricated beat pair.
    for previous, current in zip(selected, selected[1:]):
        if (
            current["index"] == previous["index"] + 1
            and 300 <= previous["ms"] <= 2000
            and 300 <= current["ms"] <= 2000
            and 0 < current["t"] - previous["t"] <= 3
            and 0 <= current["receipt"] - previous["receipt"] <= 3
        ):
            pairs.append((previous["ms"], current["ms"]))
    differences = np.array(
        [current - previous for previous, current in pairs], dtype=float
    )
    # A small minimum is an app guard, not a validation criterion.
    sufficient = len(valid) >= 30 and len(pairs) >= 20
    values = [r["ms"] for r in valid]
    # RMSSD: root mean square of adjacent changes. SDRR: sample standard
    # deviation of intervals. pRR50: percentage of adjacent changes over 50 ms.
    return dict(
        **stats(values),
        observations=len(selected),
        excluded=len(selected) - len(valid),
        pairs=pairs,
        rmssd=float(np.sqrt(np.mean(differences**2))) if sufficient else None,
        sdrr=float(np.std(values, ddof=1)) if sufficient else None,
        prr50=float(np.mean(np.abs(differences) > 50) * 100) if sufficient else None,
        sufficient=sufficient,
        valid_rows=valid,
    )


def coverage(intervals, start, end):
    """Measure the union of recorded intervals within the selected range."""
    merged = []
    # Clip intervals to the selection and merge overlaps to avoid double counting.
    for a, b in sorted(
        (max(a, start), min(b, end)) for a, b in intervals if b > start and a < end
    ):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return min(100.0, 100 * sum(b - a for a, b in merged) / max(end - start, 1e-9))


class SessionAnalysis:
    """Load verified observations and summaries for one finished session.

    Keep original RR order for adjacency checks, aggregate ECG and motion
    in one-second bins, and load detailed ECG only when requested.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        if (self.path / "active.json").exists() or self.manifest["status"] not in (
            "complete",
            "recovered",
        ):
            raise ValueError("Stop or recover the session before opening analysis.")
        self.integrity = verify_session(self.path, persist=False)
        self.issues = list(self.integrity["errors"])
        self.events = []
        self.hr = []
        self.rr = []
        self.ecg_chunks = []
        self.ecg_intervals = []
        self.acc_intervals = []
        self.resp = []
        self.resp_algorithm = "Not available"
        self.counts = defaultdict(int)
        self.invalid = defaultdict(int)
        self.ecg_bins = {}
        self.motion = {}
        self.duration = max(0, float(self.manifest.get("duration_s", 0)))
        self.observed_end = 0
        self._read_streams()
        if self.duration <= 0:
            self.duration = max(0.01, self.observed_end)
        self._read_events()
        self._read_sound_labels()
        self._read_respiration()

    def _read_streams(self):
        """Read each verified chunk once and exclude malformed observations."""
        seen = defaultdict(set)
        previous = {}
        intervals = {"ecg": self.ecg_intervals, "accelerometer": self.acc_intervals}
        # Manifest duration is authoritative when available. Recovered sessions may extend to the last valid sample.
        for entry in sorted(
            self.integrity["files"], key=lambda e: (e["signal"], e["sequence"])
        ):
            stream = entry["signal"]
            if stream == "packets" or entry["sequence"] in seen[stream]:
                continue
            seen[stream].add(entry["sequence"])
            chunk = read_chunk(self.path / entry["path"])
            if stream == "ecg":
                self.ecg_chunks.append(
                    (chunk["start_timestamp"], chunk["end_timestamp"], entry["path"])
                )
            for row in chunk["payload"]:
                index = self.counts[stream]
                self.counts[stream] += 1
                sample_time = row.get("t")
                if not finite(sample_time) or sample_time < 0:
                    self.invalid[stream] += 1
                    continue
                self.observed_end = max(self.observed_end, sample_time)
                if stream == "hr":
                    value = row.get("bpm")
                    if finite(value) and value > 0:
                        self.hr.append({"t": sample_time, "value": value})
                    else:
                        self.invalid[stream] += 1
                elif stream == "rr":
                    ticks = row.get("rr_ticks")
                    value = ticks * 1000 / 1024 if finite(ticks) else row.get("rr_ms")
                    if finite(value):
                        self.rr.append(
                            {
                                "t": sample_time,
                                "ms": value,
                                "index": index,
                                "receipt": row.get(
                                    "host_receipt_monotonic_s", sample_time
                                ),
                            }
                        )
                    else:
                        self.invalid[stream] += 1
                elif stream in intervals:
                    hz = 130 if stream == "ecg" else 50
                    values = (
                        [row.get("uv")]
                        if stream == "ecg"
                        else [row.get(k) for k in ("x_mg", "y_mg", "z_mg")]
                    )
                    if not all(finite(v) for v in values):
                        self.invalid[stream] += 1
                        continue
                    end = sample_time + 1 / hz
                    segments = intervals[stream]
                    if segments and 0 < sample_time - previous.get(stream, -1) < 3 / hz:
                        segments[-1][1] = max(segments[-1][1], end)
                    else:
                        segments.append([sample_time, end])
                    previous[stream] = sample_time
                    key = int(sample_time)
                    if stream == "ecg":
                        bin_stats = self.ecg_bins.setdefault(
                            key,
                            {
                                "count": 0,
                                "min": values[0],
                                "max": values[0],
                                "sumsq": 0,
                            },
                        )
                        bin_stats["count"] += 1
                        bin_stats["min"] = min(bin_stats["min"], values[0])
                        bin_stats["max"] = max(bin_stats["max"], values[0])
                        bin_stats["sumsq"] += values[0] ** 2
                    else:
                        bin_stats = self.motion.setdefault(
                            key,
                            {"count": 0, "sumsq": 0, "high": 0, "x": 0, "y": 0, "z": 0},
                        )
                        magnitude = math.sqrt(sum(v * v for v in values))
                        # Subtract the 1 g baseline; orientation stays in the axes.
                        deviation = abs(magnitude - 1000)
                        bin_stats["count"] += 1
                        bin_stats["sumsq"] += deviation**2
                        bin_stats["high"] += deviation > 450
                        for k, v in zip(("x", "y", "z"), values):
                            bin_stats[k] += v
        for stream, number in self.invalid.items():
            if number:
                self.issues.append(
                    {
                        "signal": stream,
                        "error": f"{number} malformed observations excluded from analysis",
                    }
                )
        self.hr.sort(key=lambda r: r["t"])

    def _read_events(self):
        """Load timestamped event logs while reporting malformed entries."""
        for path in sorted((self.path / "events").glob("*.jsonl")):
            if path.stem == "sound_labels":
                continue  # This versioned stream has its own strict validation.
            for line_no, line in enumerate(path.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not finite(row.get("t")):
                        raise ValueError("missing event time")
                    row["category"] = path.stem
                    self.events.append(row)
                except (ValueError, TypeError):
                    self.issues.append(
                        {"file": path.name, "error": f"Invalid event at line {line_no}"}
                    )
        self.events.sort(key=lambda r: r["t"])

    def _read_sound_labels(self):
        """Validate transitions without guessing labels for legacy/corrupt data."""
        self.sound_labels = []
        self.sound_labels_status = "Not labeled"
        path = self.path / "events" / "sound_labels.jsonl"
        if not path.exists():
            if "sound_labels" in self.manifest:
                self.sound_labels_status = "Labels unavailable: integrity issue"
                self.issues.append({"file": path.name, "error": "Missing sound labels"})
            return
        rows = []
        ids = set()
        try:
            with path.open() as source:
                for line in source:
                    row = json.loads(line)
                    checksum = row.pop("checksum")
                    if hashlib.sha256(canonical(row)).hexdigest() != checksum:
                        raise ValueError("checksum mismatch")
                    if (row["schema_version"] != LABEL_SCHEMA_VERSION
                        or row["session_id"] != self.manifest["session_id"]
                        or row["type"] != "sound_state"
                        or row["sequence"] != len(rows)
                        or row["label"]["value"] not in SOUND_VALUES
                        or not finite(row["t"]) or row["t"] < 0
                        or not isinstance(row["id"], str) or row["id"] in ids):
                        raise ValueError("invalid sound-state record")
                    if not rows and (row["t"] != 0 or row["label"]["value"] != "silence"):
                        raise ValueError("missing initial silence state")
                    if rows and row["t"] < rows[-1]["t"]:
                        raise ValueError("out-of-order sound state")
                    ids.add(row["id"])
                    rows.append(row)
            if not rows:
                raise ValueError("empty sound-state stream")
            if len(rows) < self.manifest.get("sound_labels", {}).get("count", 0):
                raise ValueError("missing committed sound-state records")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.sound_labels_status = "Labels unavailable: integrity issue"
            self.issues.append({"file": path.name, "error": f"Invalid sound labels: {exc}"})
            return
        self.sound_labels = rows
        self.sound_labels_status = "Recorded"

    def sound_intervals(self, start=0, end=None):
        """Return clipped [start, end) states; never bridge unknown label data."""
        end = self.duration if end is None else min(end, self.duration)
        intervals = []
        for index, row in enumerate(self.sound_labels):
            next_time = (self.sound_labels[index + 1]["t"]
                         if index + 1 < len(self.sound_labels) else self.duration)
            a, b = max(start, row["t"]), min(end, next_time)
            if a < b:
                intervals.append((a, b, row["label"]["value"]))
        return intervals

    def _read_respiration(self):
        """Read the latest saved estimates, withholding them if ECG is suspect."""
        root = self.path / "derived/respiration"
        latest = root / "latest.json"
        if any(
            e.get("signal") == "ecg" or str(e.get("file", "")).startswith("raw/ecg/")
            for e in self.issues
        ):
            self.issues.append(
                {
                    "signal": "respiration",
                    "error": "Saved estimates withheld because raw ECG has integrity issues; recalculate after review.",
                }
            )
            return
        if latest.exists():
            try:
                pointer = json.loads(latest.read_text())
                name = pointer["run"]
                if Path(name).name != name:
                    raise ValueError("Invalid respiration run path")
                run = root / name
                metadata = json.loads((run / "metadata.json").read_text())
                self.resp_algorithm = metadata.get("algorithm", "unknown")
                files = sorted(run.glob("[0-9]*.json"))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.issues.append({"signal": "respiration", "error": str(exc)})
                return
        else:
            files = sorted(root.glob("live_*.json"))
        for estimate_path in files:
            try:
                row = json.loads(estimate_path.read_text())
                window_end = row.get("end_s")
                # Old rejected windows may not have times. Do not invent a location.
                if not finite(window_end):
                    continue
                rate = row.get("rate_bpm")
                accepted = row.get("status") == "ok" and finite(rate) and rate > 0
                self.resp.append(
                    dict(
                        t=window_end,
                        value=rate if accepted else None,
                        confidence=row.get("confidence", 0),
                        status=row.get("status", "unknown"),
                    )
                )
                self.resp_algorithm = row.get("algorithm", self.resp_algorithm)
            except (OSError, ValueError, TypeError) as exc:
                self.issues.append(
                    {
                        "file": str(estimate_path.relative_to(self.path)),
                        "error": str(exc),
                    }
                )
        self.resp.sort(key=lambda r: r["t"])

    def summary(self, start=0, end=None):
        """Calculate metrics and explanatory notes for an inclusive time range."""
        end = self.duration if end is None else min(end, self.duration)
        start = max(0, start)
        if end <= start:
            raise ValueError("Choose an end time after the start time.")
        hr = [r for r in self.hr if start <= r["t"] <= end]
        rr = rr_metrics(self.rr, start, end)
        resp = [r for r in self.resp if start <= r["t"] <= end]
        accepted = [r["value"] for r in resp if r["value"] is not None]
        # Motion is summarized in one-second bins; only intersecting bin centers are used.
        motion = [(t, b) for t, b in self.motion.items() if start <= t + 0.5 <= end]
        motion_count = sum(b["count"] for _, b in motion)
        motion_rms = (
            math.sqrt(sum(b["sumsq"] for _, b in motion) / motion_count)
            if motion_count
            else None
        )
        high_pct = (
            100 * sum(b["high"] for _, b in motion) / motion_count
            if motion_count
            else None
        )
        comparison_span = min(30, (end - start) / 3)
        early = stats([r["value"] for r in hr if r["t"] < start + comparison_span])[
            "mean"
        ]
        late = stats([r["value"] for r in hr if r["t"] > end - comparison_span])["mean"]
        ecg_coverage = coverage(self.ecg_intervals, start, end)
        acc_coverage = coverage(self.acc_intervals, start, end)
        notes = []
        hr_stats = stats([r["value"] for r in hr])
        if hr_stats["count"]:
            notes.append(
                f"Reported heart rate spans {hr_stats['min']:.0f}–{hr_stats['max']:.0f} bpm, with a median of {hr_stats['median']:.1f} bpm."
            )
        if early is not None and late is not None:
            notes.append(
                f"Heart rate averaged {early:.1f} bpm in the first {comparison_span:.0f}s and {late:.1f} bpm in the last {comparison_span:.0f}s of this range ({late-early:+.1f} bpm)."
            )
        if end - start < 300:
            notes.append(
                "Short selection: variability values describe this range only; compare recordings of similar length and conditions."
            )
        notes.append(
            f'{rr["count"]} RR intervals retained; {rr["excluded"]} outside the 300–2,000 ms analysis bounds. {len(rr["pairs"])} adjacent pairs used.'
        )
        if accepted:
            notes.append(
                f"{len(accepted)} of {len(resp)} saved respiration windows accepted. Each is a 60-second ECG estimate plotted at its window end."
            )
        else:
            notes.append(
                "No accepted saved respiration estimates in this range. At least 60 seconds of suitable ECG is needed; use Recalculate respiration in the library if needed."
            )
        if self.issues:
            notes.append(
                f"{len(self.issues)} data issue(s) require review in Motion & quality. Results use valid available observations."
            )
        return dict(
            start=start,
            end=end,
            hr=stats([r["value"] for r in hr]),
            hr_rows=hr,
            rr=rr,
            respiration=stats(accepted),
            resp_rows=resp,
            motion_rms=motion_rms,
            motion_high_pct=high_pct,
            ecg_coverage=ecg_coverage,
            acc_coverage=acc_coverage,
            notes=notes,
            dropouts=sum(
                e["type"] == "disconnected" and start <= e["t"] <= end
                for e in self.events
            ),
        )

    def ecg_window(self, start, end):
        """Read original ECG points from chunks overlapping the requested range."""
        points = []
        for a, b, path in self.ecg_chunks:
            if b < start or a > end:
                continue
            for row in read_chunk(self.path / path)["payload"]:
                if (
                    finite(row.get("t"))
                    and finite(row.get("uv"))
                    and start <= row["t"] <= end
                ):
                    points.append((row["t"], row["uv"]))
        return points
