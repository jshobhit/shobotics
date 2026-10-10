"""Beat-level signal maths. Pure functions over lists of `Beat`; no I/O, no Qt.

Definitions (match the Sonorium reference):
  * Artifact: RR more than 20 % from the median of the last 7 good RRs, once at
    least 5 good RRs are known. Artifacts are kept and flagged, never deleted,
    and are excluded from HR means and RMSSD.
  * RMSSD: root mean square of successive RR differences, using only pairs of
    consecutive non-artifact beats less than 2.5 s apart. None under 5 pairs.
  * Signal quality: share of artifact beats over the last 60 s.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
import statistics

ARTIFACT_TOLERANCE = 0.20  # fraction of the recent median
RECENT_GOOD = 7  # good RRs kept for the median
MIN_GOOD_FOR_ARTIFACT = 5  # median needs this many before flagging
MAX_PAIR_GAP_S = 2.5  # beats further apart break RMSSD adjacency
MIN_RMSSD_PAIRS = 5
HR_DISPLAY_BEATS = 8  # vitals: mean of the last 8 good beats
HR_SMOOTH_BEATS = 5  # chart line: 5-beat moving average
QUALITY_WINDOW_S = 60
NO_BEATS_AFTER_S = 4


@dataclass(slots=True)
class Beat:
    t: float  # monotonic seconds, on the RR clock
    rr: int  # ms
    hr: float  # 60000 / rr
    art: bool  # artifact flag
    hr_smooth: float | None  # 5-beat moving average of good beats (chart line)
    rec: bool = False  # belongs to the current recording
    labels: tuple[str, ...] | None = None  # active labels at this beat


def is_artifact(rr: float, recent_good: list[int]) -> bool:
    """True if rr deviates more than 20 % from the median of recent good RRs."""
    if len(recent_good) < MIN_GOOD_FOR_ARTIFACT:
        return False
    med = statistics.median_high(recent_good)  # same pick as the reference
    return abs(rr - med) / med > ARTIFACT_TOLERANCE


def beats_in(beats: list[Beat], t1: float, t2: float) -> list[Beat]:
    """Beats with t1 < t <= t2. Binary search: beats are always sorted by t."""
    return beats[bisect_right(beats, t1, key=_t) : bisect_right(beats, t2, key=_t)]


def series_in(points: list[tuple], t1: float, t2: float) -> list[tuple]:
    """(t, value) points with t1 < t <= t2, by binary search."""
    return points[bisect_right(points, t1, key=_t0) : bisect_right(points, t2, key=_t0)]


def rmssd_of(window: list[Beat]) -> float | None:
    """RMSSD over a slice of consecutive beats, skipping artifacts and gaps."""
    total, n = 0.0, 0
    for a, b in zip(window, window[1:]):
        if not a.art and not b.art and b.t - a.t < MAX_PAIR_GAP_S:
            d = b.rr - a.rr
            total += d * d
            n += 1
    return math.sqrt(total / n) if n >= MIN_RMSSD_PAIRS else None


def mean_hr(window: list[Beat]) -> float | None:
    """Mean HR of the non-artifact beats, or None if there are none."""
    good = [b.hr for b in window if not b.art]
    return sum(good) / len(good) if good else None


def hr_now(beats: list[Beat]) -> float | None:
    """Displayed heart rate: mean of the last 8 good beats."""
    good = []
    for b in reversed(beats):
        if not b.art:
            good.append(b.hr)
            if len(good) == HR_DISPLAY_BEATS:
                break
    return sum(good) / len(good) if good else None


def artifact_percent(window: list[Beat]) -> float:
    """Share of artifact beats in a window, in percent."""
    return 100 * sum(b.art for b in window) / len(window) if window else 0.0


def quality_grade(percent: float) -> str:
    """Good under 2 %, Fair under 5 %, otherwise Poor."""
    return "Good" if percent < 2 else "Fair" if percent < 5 else "Poor"


def pct_change(value: float | None, ref: float | None) -> float | None:
    """Percent change from a reference, or None if either side is missing."""
    if not value or not ref:
        return None
    return 100 * (value - ref) / ref


def _t(beat: Beat) -> float:
    return beat.t


def _t0(point: tuple) -> float:
    return point[0]
