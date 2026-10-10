"""The live session store: beats, ECG, label events and recording phase.

Plain Python, no Qt. The window owns one `Session` for the life of the app and
feeds it packets; charts and exports read from it. Every method that depends on
the clock takes `now` (monotonic seconds) so the logic stays testable.

Phases:
  idle  no recording yet; beats still flow for the live display
  live  recording; beats and ECG are tagged with the active labels
  done  stopped; data is frozen until exported or replaced by a new recording
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import hrv
from .config import HRV_WINDOWS, SILENCE
from .hrv import Beat

# Memory bounds for the idle live view. Never applied to a recording or to a
# stopped session that has not been replaced yet.
IDLE_MAX_BEATS, IDLE_TRIM_BEATS = 30000, 10000
IDLE_MAX_HRV, IDLE_TRIM_HRV = 8000, 3000

BEAT_RESYNC_S = 2.0  # RR clock may drift this far from arrival before resync
ECG_RESYNC_S = 1.0  # ECG clock restarts after a gap this long
REFERENCE_MIN_BEATS = 30  # good beats needed in the opening silence


# --------------------------------------------------------------------------
# Labels and events
# --------------------------------------------------------------------------


def next_set(active: frozenset[str], label: str) -> frozenset[str]:
    """Label toggle rule.

    Instruments toggle independently. Silence clears everything. An empty set
    becomes {Silence}, so something is always recorded as active.
    """
    if label == SILENCE:
        return frozenset({SILENCE})
    on = set(active) - {SILENCE}
    on ^= {label}
    return frozenset(on) if on else frozenset({SILENCE})


def intervals(events: list[dict], end: float) -> list[dict]:
    """Pair on/off events into label intervals, sorted by start.

    Each interval is {"label", "t1", "t2", "open"}; a label still on runs to
    `end` and is marked open.
    """
    started: dict[str, float] = {}
    out = []
    for e in events:
        if e["kind"] == "on":
            started[e["text"]] = e["t"]
        elif e["kind"] == "off" and e["text"] in started:
            t1 = started.pop(e["text"])
            out.append({"label": e["text"], "t1": t1, "t2": e["t"], "open": False})
    for label, t1 in started.items():
        out.append({"label": label, "t1": t1, "t2": end, "open": True})
    return sorted(out, key=lambda s: s["t1"])


def labels_at(events: list[dict], times):
    """Yield the active label tuple for each time in an ascending iterable.

    One forward pass over the events, so tagging every ECG sample is linear.
    """
    switches = [e for e in events if e["kind"] in ("on", "off")]
    on: list[str] = []
    i = 0
    for t in times:
        while i < len(switches) and switches[i]["t"] <= t:
            e = switches[i]
            if e["kind"] == "on":
                on.append(e["text"])
            elif e["text"] in on:
                on.remove(e["text"])
            i += 1
        yield tuple(sorted(on))  # same order as the beat labels


# --------------------------------------------------------------------------
# Session store
# --------------------------------------------------------------------------


class Session:
    def __init__(self) -> None:
        self.phase = "idle"
        self.exported = False
        self.ids = {"p": "", "s": "", "f": ""}
        self.docs = {"consent": [], "questionnaire": []}  # see documents.Document
        self.t0 = 0.0  # recording start, monotonic
        self.t_end = 0.0  # recording stop, monotonic
        self.started_at: datetime | None = None  # wall clock at t0, UTC
        self.active: frozenset[str] = frozenset()
        self.events: list[dict] = []  # {"t", "kind": on|off|signal|phase, "text"}
        self.beats: list[Beat] = []  # sorted by t
        self.recent_good: list[int] = []  # last good RRs for artifact detection
        self.hrv: dict[int, list[tuple]] = {w: [] for w in HRV_WINDOWS}  # (t, ms)
        self.ecg_rec: list[tuple[float, int]] = []  # (t, uV) while recording
        self._smooth: list[float] = []  # last good HRs for the chart line
        self._beat_clock = 0.0
        self._ecg_clock = 0.0

    @property
    def recording(self) -> bool:
        return self.phase == "live"

    def rel(self, t: float) -> float:
        """Seconds since recording start."""
        return t - self.t0

    # -- incoming signal ----------------------------------------------------

    def add_hr_packet(self, arrival: float, rr_list: list[int]) -> list[Beat]:
        """Place a packet's RR intervals on the beat clock and store them.

        The packet's last beat happened at about `arrival`. Beat times run on
        their own cumulative RR clock, which is smooth beat to beat; if it drifts
        more than 2 s from arrival time (gaps, reconnects) it resyncs.
        """
        new = []
        expected = arrival - sum(rr_list) / 1000
        for rr in rr_list:
            if rr <= 0:
                continue
            expected += rr / 1000
            ticked = self._beat_clock + rr / 1000
            self._beat_clock = (
                ticked
                if self._beat_clock and abs(ticked - expected) < BEAT_RESYNC_S
                else expected
            )
            new.append(self.add_beat(self._beat_clock, rr))
        return new

    def add_beat(self, t: float, rr: int) -> Beat:
        """Append one beat, flagging artifacts and tagging recording labels."""
        if self.beats and t <= self.beats[-1].t:
            t = self.beats[-1].t + 0.001  # keep beats strictly sorted
        art = hrv.is_artifact(rr, self.recent_good)
        hr = 60000 / rr
        if not art:
            self.recent_good.append(rr)
            del self.recent_good[: -hrv.RECENT_GOOD]
            self._smooth.append(hr)
            del self._smooth[: -hrv.HR_SMOOTH_BEATS]
        beat = Beat(
            t=t,
            rr=rr,
            hr=hr,
            art=art,
            hr_smooth=None if art else sum(self._smooth) / len(self._smooth),
            rec=self.recording,
            labels=tuple(sorted(self.active)) if self.recording else None,
        )
        self.beats.append(beat)
        if self.phase == "idle" and len(self.beats) > IDLE_MAX_BEATS:
            del self.beats[:IDLE_TRIM_BEATS]
        return beat

    def add_ecg(self, arrival: float, samples: list[int]) -> None:
        """Timestamp ECG samples at 130 Hz and keep them while recording.

        Samples are spaced exactly 1/130 s apart. After a gap of over 1 s the
        clock restarts so the packet ends at its arrival time.
        """
        if arrival - self._ecg_clock > ECG_RESYNC_S:
            self._ecg_clock = arrival - len(samples) / 130
        for v in samples:
            self._ecg_clock += 1 / 130
            if self.recording:
                self.ecg_rec.append((self._ecg_clock, v))

    def sample_hrv(self, now: float, connected: bool) -> None:
        """Once a second: rolling RMSSD for every window, so switching is instant."""
        for w, series in self.hrv.items():
            series.append((now, self.rmssd(now - w, now) if connected else None))
            if self.phase == "idle" and len(series) > IDLE_MAX_HRV:
                del series[:IDLE_TRIM_HRV]

    # -- labels and phase ---------------------------------------------------

    def log(self, kind: str, text: str, t: float) -> None:
        self.events.append({"t": t, "kind": kind, "text": text})

    def apply_active(self, nxt: frozenset[str], t: float) -> None:
        """Switch the active set, logging an off/on event for each change."""
        for label in sorted(self.active - nxt):
            self.log("off", label, t)
        for label in sorted(nxt - self.active):
            self.log("on", label, t)
        self.active = nxt

    def toggle(self, label: str, now: float) -> None:
        if self.recording:
            self.apply_active(next_set(self.active, label), now)

    def since_on(self, label: str, now: float) -> float:
        """Time the label was last switched on (for its running timer)."""
        for e in reversed(self.events):
            if e["kind"] == "on" and e["text"] == label:
                return e["t"]
        return now

    def start(self, ids: dict, docs: dict, now: float) -> None:
        """Begin a recording. It always opens in Silence."""
        self.ids = dict(ids)
        self.docs = docs
        for b in self.beats:  # earlier beats are live-view only
            b.rec, b.labels = False, None
        self.t0 = now
        self.started_at = datetime.now(timezone.utc)
        self.events = []
        self.ecg_rec = []
        self.exported = False
        self.active = frozenset()
        self.phase = "live"
        self.apply_active(frozenset({SILENCE}), now)

    def stop(self, now: float) -> None:
        """Close every open label interval and freeze the session."""
        self.t_end = now
        self.apply_active(frozenset(), now)
        self.log("phase", "Recording stopped", now)
        self.phase = "done"

    # -- derived values -----------------------------------------------------

    def end_time(self, now: float) -> float:
        return self.t_end if self.phase == "done" else now

    def intervals(self, now: float) -> list[dict]:
        return intervals(self.events, self.end_time(now))

    def recorded(self, window: list[Beat]) -> list[Beat]:
        return [b for b in window if b.rec]

    def rmssd(self, t1: float, t2: float) -> float | None:
        return hrv.rmssd_of(hrv.beats_in(self.beats, t1, t2))

    def is_opening(self, interval: dict) -> bool:
        """The opening Silence: the interval that starts with the recording."""
        return interval["label"] == SILENCE and interval["t1"] - self.t0 < 0.5

    def reference(self, now: float) -> dict | None:
        """Baseline for % change: the opening Silence period.

        Returns {"open", "ok", "hr", "rmssd", "beats"} or None before any
        recording. "ok" needs at least 30 good beats.
        """
        if self.phase == "idle":
            return None
        opening = next((s for s in self.intervals(now) if self.is_opening(s)), None)
        if opening is None:
            return None
        w = self.recorded(hrv.beats_in(self.beats, opening["t1"], opening["t2"]))
        return {
            "open": self.recording and opening["open"],
            "ok": sum(not b.art for b in w) >= REFERENCE_MIN_BEATS,
            "hr": hrv.mean_hr(w),
            "rmssd": hrv.rmssd_of(w),
            "beats": len(w),
        }

    def segment_stats(self, now: float) -> list[dict]:
        """Per-interval mean HR, RMSSD and % change against the opening silence."""
        ref = self.reference(now)
        base = ref if ref and ref["ok"] else None
        out = []
        for s in self.intervals(now):
            w = self.recorded(hrv.beats_in(self.beats, s["t1"], s["t2"]))
            hr, rm = hrv.mean_hr(w), hrv.rmssd_of(w)
            compare = base and not self.is_opening(s)
            out.append(
                {
                    **s,
                    "hr": hr,
                    "rmssd": rm,
                    "d_hr": hrv.pct_change(hr, base["hr"]) if compare else None,
                    "d_rmssd": hrv.pct_change(rm, base["rmssd"]) if compare else None,
                }
            )
        return out
