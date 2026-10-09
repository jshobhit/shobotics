# Svaram · Polar H10 Measurement Rig

A standalone, offline Python desktop app for macOS. Native Qt window; no browser, web server, account, network service, or cloud storage. The included **Svaram H10.app** bundles Python and its dependencies. Source code and tests are included alongside it.

## Launch

Double-click **Svaram H10.app** in this folder. Built for this Apple Silicon Mac (macOS 14 or newer). No Python installation, package download, or Internet connection is needed to run the bundle. Keep the app in this writable folder: its sessions are stored alongside it in `sessions/`. The first Bluetooth scan may prompt for macOS Bluetooth permission. Enable access in System Settings → Privacy & Security → Bluetooth if previously denied.

1. Wear the H10 strap with moistened electrodes. Close other apps using its ECG stream.
2. Click **Scan for H10**, select the named sensor/identifier, then **Connect**.
3. Inspect the ECG trace and signal-quality status. Starting is enabled once ECG and motion packets arrive.
4. Optionally enter a participant code, then **Start recording**. Saving is automatic every five seconds.
5. Click **Stop & save**. This stops sensor streams, disconnects, commits the final chunks, marks the session complete, and calculates respiration from saved ECG.
6. In **Saved sessions**, select a session. **Verify & open** checks checksums, displays metadata/events and opens the first eight seconds of saved ECG. **Export ZIP + CSV** includes the original files, derived output, manifest, connection/quality events, integrity report, and per-stream CSV files.

**Use simulator** runs synthetic ECG, HR/RR and motion without Bluetooth. All simulated sessions carry a `simulated: true` manifest flag and a SIMULATED library label. **Test 20s dropout** leaves a real gap in the simulated recording while its session clock continues. Simulator results are not physiological measurements.

## Session analysis dashboard · version 0.2

In **Saved sessions**, select a recording and click **Open analysis**, or double-click the session row. Each session opens in an independent, non-modal analysis window; multiple sessions can stay open side by side. Reopening the same session focuses its existing window. Closing analysis does not affect recording or the library. Saved sessions can be reviewed during another recording; active sessions must first be stopped or recovered. Analysis is fully offline and reads saved data without rewriting the raw files, manifest, or respiration outputs.

### Recording sound labels

Recording starts in **Silence**. The five sound buttons (Fire, Water, Wind, Earth, Space) appear only while recording. Select one to switch the current state; click the selected button again to return to silence. Only one element can be active. A single-line description shows the saved state. These labels are independent of the Rest/Sound/Recovery study-phase markers, and are manual annotations, not microphone recordings or automatic sound detection.

State transitions are append-only records in `events/sound_labels.jsonl`, immediately flushed and synced before the displayed state changes. Each contains `schema_version`, `session_id`, UUID `id`, consecutive zero-based `sequence`, `type: "sound_state"`, session-relative monotonic `t` in seconds, write-time `utc`, a `label` object (`{"value": "fire"}`), and a SHA-256 `checksum` of the canonical JSON excluding that checksum. UI transitions capture the button-press time. An initial silence record is persisted at `t: 0`; repeated states do not generate duplicate records. Storage grows with transitions, not sensor sample count. Raw samples are not modified.

A label applies over `[t, next_t)`, with the last state extending to the saved session duration. The session clock and label state continue through sensor dropouts. After recovery, the last label extends only to the recovered duration, not an inferred crash time. ZIP exports include the original label stream. Analysis validates checksums, identity, vocabulary, sequence and ordering, and displays a colored state strip on time-series charts. Missing labels in older sessions are **Not labeled**, not assumed silence; invalid streams are reported and withheld. No label editing or label-based filtering is provided. Checksums detect accidental edits/corruption, not malicious tampering; hand editing JSON requires updating checksums and preserving stream invariants.

Five views are available:

- **Overview**: mean heart rate, exploratory RR variability, mean derived respiration, ECG time coverage, trend charts, and descriptive range comparisons.
- **RR & variability**: original RR intervals, excluded observations, RMSSD, SDRR, pRR50, 60-second rolling RMSSD, and a Poincaré plot of adjacent intervals.
- **Respiration**: saved ECG-derived rate estimates, accepted-window counts, rate range, spectral concentration and algorithm identity. Concentration is an algorithm score, not a probability of accuracy.
- **Motion & quality**: one-second motion and acceleration-axis trends, high-motion sample proportion, timestamp coverage, connection/quality events, and integrity issues.
- **ECG explorer**: a movable 5/10/20-second raw ECG window and a full-range amplitude overview. Gaps are kept visible.

Use **Analyze range → Apply range** to recompute the panels for part of the session. **Full session** resets the range. Hover over charts for source values. Respiration points are selected by their 60-second window end; their source window may begin before the selected range. Motion is aggregated into one-second bins selected by midpoint. Large ECG recordings are read by chunk for the selected detail window.

### Analysis definitions

`session-overview-1.0.0` calculates arithmetic summaries of reported HR observations. RR analysis retains intervals from 300 through 2,000 ms; this is a basic plausibility filter, not normal-beat classification. RMSSD is the square root of the mean squared difference between eligible adjacent RR intervals. SDRR is the sample standard deviation of retained RR intervals; pRR50 is the percentage of eligible adjacent RR differences whose absolute value exceeds 50 ms. The dashboard deliberately does not label these SDRR/pRR50 values SDNN/pNN50 because normal sinus beats have not been identified. Rejected observations, missing original indices, backwards timestamps and gaps greater than three seconds break adjacency. Nothing is corrected or interpolated. At least 30 retained intervals and 20 eligible pairs are required to show variability values; that minimum is a display guard, not a clinical validation criterion.

The motion proxy is RMS of the deviation of acceleration magnitude from 1,000 mg. “High motion” means deviation above 450 mg, matching the existing recorder's heuristic. Coverage is the union of valid sample-time intervals divided by selected duration. It indicates availability, not signal quality. Corrupt raw chunks are reported and excluded; saved respiration is withheld if raw ECG integrity is compromised. Short ranges and unavailable metrics have explicit messages. No health diagnosis, stress score, sleep score or autonomic-balance score is inferred.

Metric terminology was checked against PhysioNet's [RR/HRV tutorial](https://archive.physionet.org/tutorials/hrv/) and [HRV Toolkit definitions](https://archive.physionet.org/tutorials/hrv-toolkit/). Compare recordings made over similar durations and conditions; interpretation of derived physiology still requires validation.

## Recovery and preservation

On restart, active-session markers identify incomplete recordings. Select the session in Saved sessions and choose **Recover incomplete**. Recovery preserves raw files, verifies independent chunks and marks the manifest `recovered`; it does not pretend the original stop time is known. The last uncommitted interval, normally at most about five seconds, may be lost after a crash. Persistent disk errors stop acquisition visibly and leave an incomplete session for recovery.

Each raw chunk has session and stream identity, a sequence number, sampling information, timestamps, count, payload, and SHA-256 checksum. Chunks are written to unique temporary files, flushed with `fsync`, read back, and atomically published without replacing existing raw chunks. Completed raw files are read-only. The manifest is replaced atomically. Events are append-only JSONL flushed to disk. An orphan committed chunk can be reconciled after a crash between chunk publication and manifest update. Temporary files, missing files and corrupt files are preserved and reported. Other valid chunks remain readable.

Five-second chunks deliberately improve on the PRD's ten-second target to keep the normal unsaved interval below ten seconds. Acquisition, checksumming, disk writes, and live analysis run on a separate thread from the window; rendering is not in the recording path. Memory retains only a small live trace, one derived-analysis window, short pending chunks, and chunk metadata. Opening/exporting/recalculating is streamed through independent chunks.

## Recorded signals and time

- ECG: signed raw microvolt values, 130 Hz, PMD raw frame 0.
- Accelerometer: signed raw x/y/z milligravity values, 50 Hz, 16-bit, ±8g setting; raw PMD frames 0/1/2 decode according to their byte width.
- Heart rate: Bluetooth Heart Rate Measurement value and original flags/energy/contact fields.
- RR: original integer ticks in 1/1024 second plus an explicitly calculated millisecond column.
- Packets: the original PMD and HR notification bytes as hex, with host receipt timestamps, for audit or future decoding.

PMD packet end timestamps are retained as integer nanoseconds. Per-sample sensor timestamps are reconstructed backwards at the negotiated rate (or from consecutive packet times within 3% of that rate). A shared per-connection sensor/host monotonic anchor aligns ECG and acceleration; anchor events and each packet's receipt time are retained. After reconnect or clock reset a new anchor is logged. Gaps are never interpolated into raw data. RR beat endpoints are reconstructed backwards from host receipt time; HR/RR have no supplied sensor clock and carry unknown BLE transport latency. UTC start time is an external reference, not the session clock. Timing is not calibrated for external instrument synchronization.

Automatic reconnect uses a 1–10 second backoff and keeps an active session open. The app checks PMD settings and start acknowledgments. Unsupported/compressed PMD frames are reported; their original bytes remain saved. This MVP targets H10 raw streams, not other Polar models or arbitrary firmware extensions.

## Derived respiration and quality

Respiration is **ECG-derived**, never a directly measured respiratory channel. Algorithm `edr-r-amplitude-1.0.0` uses fixed 60-second windows at 10-second intervals, ECG filtering (5–25 Hz), R-peak amplitude modulation, interpolation to 4 Hz, a 0.1–0.5 Hz respiration band and a spectral peak estimate. It rejects gaps, flat/clipped ECG, unreliable beat series and weak/low-confidence modulation. No number is shown until sufficient saved ECG is available. The supported estimate range is 6–30 breaths/min. These heuristic estimates require empirical validation and are not diagnostic measurements.

**Recalculate respiration** creates a new versioned run with original raw ECG unchanged. Each run contains derived signal and rate CSVs, JSON windows, algorithm version, parameters, source integrity issues, and numerical-library versions. Identical raw input produces identical numeric output with the bundled runtime. Raw records are committed before live derivation. Motion and poor-contact events are available for assessing the estimates, but the respiration estimator does not yet use motion to reject windows.

Basic quality flags cover H10 electrode-contact status (when supported), flat/clipped ECG, missing/stale streams, sensor-time discontinuities, unexpected data rate and excessive motion. They are heuristic warnings; poor-quality raw data is never deleted.

## Directory layout

```
app/
  Svaram H10.app/        # double-clickable app, bundled runtime
  main.py
  h10rig/               # Python source
  tests/
  requirements*.txt
  requirements-lock.txt
  Svaram.spec            # reproducible bundle recipe
  README.md
  VALIDATION.md
  sessions/
    SESSION_ID/
      manifest.json
      active.json       # present only while active/incomplete
      raw/{ecg,hr,rr,accelerometer,packets}/00000000.json
      events/{connection,quality}.jsonl
      derived/respiration/
      verification.json
```

## Development

The bundle runs offline without setup. To edit/rebuild from source, Python 3.12 and dependencies are required (a fresh dependency installation needs Internet or a wheel cache):

```
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python main.py
.venv/bin/python -m pytest tests -q
.venv/bin/python -m black h10rig tests main.py
.venv/bin/python -m PyInstaller --noconfirm Svaram.spec
```

You may set `PYINSTALLER_CONFIG_DIR` to a writable cache folder. `main.py --demo` opens the simulator. `--data-root PATH` is a developer/test option for keeping test sessions separate. Do not modify source files inside a compiled bundle; rebuild after source changes.

## Protocol references

The implementation was checked against Polar's official PMD parsing/setting definitions and Bleak's macOS documentation:

- https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/pmd/model/AccData.kt
- https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/pmd/PmdSetting.kt
- https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/pmd/PmdControlPointResponse.kt
- https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/pmd/model/PmdTimeStampUtils.kt
- https://bleak.readthedocs.io/en/latest/backends/macos.html

See VALIDATION.md for checks performed and remaining hardware acceptance work.
