"""
Reproducible exploratory ECG-derived respiration, not direct respiration.

    Fixed 60-second windows
    10-second stride; 
    reject gaps/flatline/unreliable peak series.
    `
"""

from collections import deque
from pathlib import Path
import csv
import uuid

import numpy as np
import scipy
from scipy.signal import butter, sosfiltfilt, find_peaks, welch, detrend

from .storage import atomic_json, iter_rows, utc, verify_session

ALGORITHM = "edr-r-amplitude-1.0.0"
PARAMETERS = {
    "window_s": 60,
    "stride_s": 10,
    "ecg_band_hz": [5, 25],
    "resp_band_hz": [0.1, 0.5],
    "resample_hz": 4,
    "min_peak_count": 30,
    "max_gap_s": 0.05,
    "min_confidence": 0.35,
}


def derive(rows):
    """Estimate respiration from changes in ECG beat amplitudes.

    Reject short, gapped, or unreliable windows. The confidence value is
    spectral concentration, not a probability that the rate is correct.
    """
    result = {
        "algorithm": ALGORITHM,
        "status": "insufficient_data",
        "rate_bpm": None,
        "confidence": 0.0,
        "derived": True,
        "signal": [],
    }

    # Reject unsuitable windows before any filtering or interpolation.
    if len(rows) < 7000:
        return result
    times = np.array([r["t"] for r in rows])
    ecg_uv = np.array([r["uv"] for r in rows], dtype=float)
    result.update(start_s=float(times[0]), end_s=float(times[-1]))
    if (
        times[-1] - times[0] < 58
        or np.any(np.diff(times) <= 0)
        or np.max(np.diff(times)) > 0.05
    ):
        result["status"] = "gap_or_short_window"
        return result
    if np.ptp(ecg_uv) < 40 or np.max(np.abs(ecg_uv)) > 30000:
        result["status"] = "poor_ecg"
        return result

    # Isolate beat energy and detect peaks regardless of ECG polarity.
    filtered = sosfiltfilt(
        butter(3, [5, 25], btype="bandpass", fs=130, output="sos"), ecg_uv
    )
    peaks, _ = find_peaks(
        np.abs(filtered),
        distance=int(0.32 * 130),
        prominence=max(40, np.std(filtered) * 0.8),
    )
    if len(peaks) < 30:
        result["status"] = "too_few_beats"
        return result
    intervals = np.diff(times[peaks])
    if (
        np.median(intervals) < 0.35
        or np.median(intervals) > 1.5
        or np.max(intervals) > 2.2
    ):
        result["status"] = "unreliable_beats"
        return result

    # Resample beat amplitudes at 4 Hz; this derived envelope is not raw ECG.
    grid = np.arange(times[peaks[0]], times[peaks[-1]], 0.25)
    amplitude = np.abs(filtered[peaks])
    envelope = np.interp(grid, times[peaks], amplitude)
    if np.std(envelope) < max(2, np.median(amplitude) * 0.005):
        result["status"] = "weak_modulation"
        return result

    # Keep 0.1–0.5 Hz modulation and locate its strongest spectral peak.
    respiration_signal = sosfiltfilt(
        butter(2, [0.1, 0.5], btype="bandpass", fs=4, output="sos"), detrend(envelope)
    )
    frequencies, power = welch(
        respiration_signal, fs=4, nperseg=min(240, len(respiration_signal)), nfft=2048
    )
    band = (frequencies >= 0.1) & (frequencies <= 0.5)
    band_frequencies, band_power = frequencies[band], power[band]
    peak = int(np.argmax(band_power))
    peak_frequency = band_frequencies[peak]

    # Score the fraction of in-band power near the dominant frequency.
    confidence = float(
        band_power[np.abs(band_frequencies - peak_frequency) < 0.035].sum()
        / max(band_power.sum(), 1e-12)
    )
    result.update(
        status="ok" if confidence >= 0.35 else "low_confidence",
        confidence=round(confidence, 4),
        rate_bpm=round(float(peak_frequency * 60), 1) if confidence >= 0.35 else None,
        signal=[
            {"t": round(float(a), 6), "amplitude_uv": round(float(b), 6)}
            for a, b in zip(grid, respiration_signal)
        ],
    )
    return result


class WindowProcessor:
    """Feed overlapping 60-second ECG windows at ten-second boundaries."""

    def __init__(self):
        self.rows = deque(maxlen=8000)
        self.next_end = 60.0

    def feed(self, rows):
        """Return estimates for newly crossed boundaries, skipping missed windows."""
        results = []
        for row in rows:
            self.rows.append(row)
            if row["t"] >= self.next_end:
                end = self.next_end
                results.append(
                    derive([r for r in self.rows if end - 60 <= r["t"] < end])
                )

                # After a gap, resume at the next stride rather than inventing missed windows.
                self.next_end = (int(row["t"] // 10) + 1) * 10
        return results


def recalculate(path):
    """Save a new derived run from raw ECG, then publish its latest-run pointer."""
    path = Path(path)
    if (path / "active.json").exists():
        raise ValueError("Stop or recover the session first")
    report = verify_session(path)
    run = path / "derived" / "respiration" / ("run_" + uuid.uuid4().hex[:10])
    run.mkdir()
    processor = WindowProcessor()
    count = 0
    with (
        (run / "signal.csv").open("w", newline="") as signal_file,
        (run / "rates.csv").open("w", newline="") as rate_file,
    ):
        signal_writer = csv.writer(signal_file)
        signal_writer.writerow(["window_end_s", "t", "derived_edr_amplitude_uv"])
        rate_writer = csv.writer(rate_file)
        rate_writer.writerow(
            ["window_end_s", "rate_bpm", "confidence", "status", "algorithm"]
        )
        for row in iter_rows(path, "ecg"):
            for result in processor.feed([row]):
                atomic_json(run / f"{count:06d}.json", result, exclusive=True)
                count += 1
                end = result.get("end_s", row["t"])
                rate_writer.writerow(
                    [
                        end,
                        result["rate_bpm"],
                        result["confidence"],
                        result["status"],
                        ALGORITHM,
                    ]
                )
                signal_writer.writerows(
                    [end, p["t"], p["amplitude_uv"]] for p in result["signal"]
                )
    atomic_json(
        run / "metadata.json",
        dict(
            algorithm=ALGORITHM,
            parameters=PARAMETERS,
            created_at=utc(),
            numpy_version=np.__version__,
            scipy_version=scipy.__version__,
            derived=True,
            source="raw/ecg",
            windows=count,
            source_integrity_errors=report["errors"],
            note="Exploratory ECG amplitude modulation. Not a directly measured respiratory signal; no clinical validation.",
        ),
    )

    # Publish this run only after its result files and metadata are saved.
    atomic_json(
        path / "derived" / "respiration" / "latest.json",
        {"run": run.name, "algorithm": ALGORITHM},
    )
    return run
