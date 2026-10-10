import pytest

from h10rig.config import SILENCE
from h10rig.session import Session, intervals, labels_at, next_set

GONG, DRUM = "Plate Gong", "Ocean Drum"


def test_next_set_rules():
    s = frozenset({SILENCE})
    s = next_set(s, GONG)
    assert s == {GONG}  # an instrument replaces Silence
    s = next_set(s, DRUM)
    assert s == {GONG, DRUM}  # instruments stack
    s = next_set(s, GONG)
    assert s == {DRUM}  # and toggle independently
    assert next_set(s, DRUM) == {SILENCE}  # empty becomes Silence
    assert next_set(frozenset({GONG, DRUM}), SILENCE) == {SILENCE}  # Silence clears


def test_intervals_pair_events_and_keep_open_ones():
    ev = [
        {"t": 0, "kind": "on", "text": SILENCE},
        {"t": 5, "kind": "off", "text": SILENCE},
        {"t": 5, "kind": "on", "text": GONG},
        {"t": 7, "kind": "on", "text": DRUM},
        {"t": 9, "kind": "off", "text": GONG},
        {"t": 9, "kind": "signal", "text": "Sensor lost"},
    ]
    out = intervals(ev, end=12)
    assert [(i["label"], i["t1"], i["t2"], i["open"]) for i in out] == [
        (SILENCE, 0, 5, False),
        (GONG, 5, 9, False),
        (DRUM, 7, 12, True),
    ]


def test_labels_at_follows_events():
    ev = [
        {"t": 0, "kind": "on", "text": SILENCE},
        {"t": 2, "kind": "off", "text": SILENCE},
        {"t": 2, "kind": "on", "text": GONG},
    ]
    assert list(labels_at(ev, [1, 2, 3])) == [(SILENCE,), (GONG,), (GONG,)]


def recording(t0=100.0):
    s = Session()
    s.start(
        {"p": "PT-1", "s": "S-1-1", "f": "FC-1"},
        {"consent": [], "questionnaire": []},
        t0,
    )
    return s


def test_recording_starts_in_silence_and_stop_closes_everything():
    s = recording()
    assert s.active == {SILENCE}
    s.toggle(GONG, 110)
    s.toggle(DRUM, 120)
    s.stop(130)
    kinds = [(e["kind"], e["text"], e["t"]) for e in s.events]
    assert kinds == [
        ("on", SILENCE, 100),
        ("off", SILENCE, 110),
        ("on", GONG, 110),
        ("on", DRUM, 120),
        ("off", DRUM, 130),
        ("off", GONG, 130),
        ("phase", "Recording stopped", 130),
    ]
    assert s.phase == "done" and not s.active


def test_toggle_is_ignored_unless_recording():
    s = Session()
    s.toggle(GONG, 1)
    assert s.events == [] and s.active == frozenset()


def test_beats_carry_active_labels_and_stay_sorted():
    s = recording()
    s.toggle(GONG, 100.5)
    b1 = s.add_beat(101.0, 800)
    b2 = s.add_beat(100.9, 800)  # out of order arrival
    assert b1.labels == (GONG,) and b1.rec
    assert b2.t > b1.t


def test_beat_clock_runs_on_rr_and_resyncs_after_drift():
    s = Session()
    s.add_hr_packet(10.0, [1000])
    s.add_hr_packet(11.05, [1000])  # 50 ms jitter: stays on the RR clock
    assert [b.t for b in s.beats] == pytest.approx([10.0, 11.0])
    s.add_hr_packet(20.0, [1000])  # 9 s gap: resync to arrival
    assert s.beats[-1].t == pytest.approx(20.0)


def test_ecg_samples_are_spaced_at_130_hz_and_kept_only_while_recording():
    s = Session()
    s.add_ecg(5.0, [1, 2, 3])
    assert s.ecg_rec == []
    s = recording(t0=0)
    s.add_ecg(5.0, [1] * 130)
    s.add_ecg(6.0, [2] * 130)
    times = [t for t, _ in s.ecg_rec]
    assert times[-1] == pytest.approx(6.0)
    assert times[1] - times[0] == pytest.approx(1 / 130)


def fill(s, t1, t2, rr=1000):
    t = t1
    while t < t2:
        s.add_beat(t, rr)
        t += rr / 1000


def test_reference_is_the_opening_silence():
    s = recording(t0=0)
    fill(s, 0.5, 20)
    ref = s.reference(20)
    assert ref["open"] and not ref["ok"]  # under 30 beats, still open
    fill(s, 20, 45)
    s.toggle(GONG, 45)
    fill(s, 45.5, 60, rr=800)
    ref = s.reference(60)
    assert ref["ok"] and not ref["open"]
    assert ref["hr"] == pytest.approx(60)
    stats = s.segment_stats(60)
    gong = next(x for x in stats if x["label"] == GONG)
    assert gong["d_hr"] == pytest.approx(25)  # 75 bpm vs 60 bpm
    assert next(x for x in stats if x["label"] == SILENCE)["d_hr"] is None


def test_stopped_session_is_never_trimmed():
    s = recording(t0=0)
    fill(s, 1, 10)
    s.stop(10)
    recorded = sum(b.rec for b in s.beats)
    for i in range(31000):  # beyond the idle cap
        s.add_beat(11 + i, 1000)
    assert sum(b.rec for b in s.beats) == recorded
