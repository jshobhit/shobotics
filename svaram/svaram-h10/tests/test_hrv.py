import math

import pytest

from h10rig import hrv
from h10rig.hrv import Beat


def beat(t, rr, art=False):
    return Beat(t=t, rr=rr, hr=60000 / rr, art=art, hr_smooth=None)


def test_artifact_needs_five_good_rrs():
    assert not hrv.is_artifact(2000, [800] * 4)
    assert hrv.is_artifact(2000, [800] * 5)


def test_artifact_threshold_is_20_percent_of_median():
    recent = [790, 800, 800, 810, 820, 800, 800]
    assert not hrv.is_artifact(955, recent)  # +19.4 %
    assert hrv.is_artifact(965, recent)  # +20.6 %
    assert hrv.is_artifact(635, recent)  # -20.6 %


def test_rmssd_known_value():
    rrs = [800, 810, 790, 820, 800, 805]
    beats = [beat(i * 0.8, rr) for i, rr in enumerate(rrs)]
    diffs = [10, -20, 30, -20, 5]
    expected = math.sqrt(sum(d * d for d in diffs) / len(diffs))
    assert hrv.rmssd_of(beats) == pytest.approx(expected)


def test_rmssd_skips_artifacts_and_gaps():
    beats = [beat(i * 0.8, 800 + (i % 2) * 20) for i in range(8)]  # 7 pairs of 20 ms
    assert hrv.rmssd_of(beats) == pytest.approx(20)
    beats[3].art = True  # drops pairs (2,3) and (3,4): 5 left
    assert hrv.rmssd_of(beats) == pytest.approx(20)
    for b in beats[6:]:
        b.t += 3  # a 3 s gap drops pair (5,6): 4 left, too few
    assert hrv.rmssd_of(beats) is None


def test_rmssd_needs_five_pairs():
    assert hrv.rmssd_of([beat(i, 800) for i in range(5)]) is None
    assert hrv.rmssd_of([beat(i * 0.8, 800) for i in range(6)]) == 0


def test_beats_in_is_half_open_by_time():
    beats = [beat(t, 800) for t in (1.0, 2.0, 3.0, 4.0)]
    assert [b.t for b in hrv.beats_in(beats, 1.0, 3.0)] == [2.0, 3.0]


def test_hr_now_uses_last_eight_good_beats():
    beats = [beat(i, 1000) for i in range(10)] + [beat(10, 500, art=True)]
    assert hrv.hr_now(beats) == pytest.approx(60)


def test_quality_grades():
    assert hrv.quality_grade(1.9) == "Good"
    assert hrv.quality_grade(4.9) == "Fair"
    assert hrv.quality_grade(5.0) == "Poor"
