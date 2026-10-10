# Sonorium session desk · Svaram H10 v0.3

A Python desktop app (PySide6) for recording instrumented sound-therapy
sessions with a Polar H10: live heart rate, HRV and ECG, instrument labels
toggled as they play, and one-click exports. Everything runs on this Mac; no
browser, server or network is involved.

## Run

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python main.py
```

The first connection asks for Bluetooth permission. If it was denied, enable
it in System Settings › Privacy & Security › Bluetooth.

To rebuild the double-clickable app, double-click `build-app.command`. It
runs the tests, a short launch from source, then PyInstaller, and copies the
result to `Svaram H10.app` in this folder. Everything it prints goes to
`build.log`. It uses the virtual environment at `../.venv`.

## Session flow

1. Wear the strap with moistened electrodes. Click **Connect Polar H10**. The
   app scans for the first sensor whose name starts with "Polar H10".
2. Press **N** (or **New recording**). Attach the consent form and the
   questionnaire (drop files or **Attach**), check the three IDs, press **↵**.
   Start is enabled only with both documents, all IDs and a connected sensor.
3. The recording opens in **Silence**. Toggle instruments with the rail or
   their hotkeys as they start and stop. Several can be on at once; **0**
   (Silence) clears them; with nothing on, the label is Silence.
4. **Hold to stop** for 1.2 s (mouse, Space or Enter). Review opens with the
   per-label table and the exports.
5. Save the **session package**. Quitting or starting a new recording with an
   unsaved session asks first.

If the strap drops out mid-session the recording keeps running, a "Sensor
lost" event is logged, and **Reconnect** reuses the same strap.

### Hotkeys

| Family | Labels |
| --- | --- |
| Control | Silence `0` |
| Gongs and metal | Plate Gong `1`, Plate Bells `2`, Tubular Bells `3`, Spiral Chime `4`, Tubular Resonators `5`, Angel Chimes `6` |
| Drums and rattles | Storm Drum `Q`, Shamanic Drum `W`, Ocean Drum `E`, Rattles `R` |
| Stone, glass, water | Sound Stone `A`, Glassophone `S`, Water Bowl `D` |
| Bed, breath, voice | Sound Bed `Z`, Flutes `X`, Wind Breath `C`, Natural Voice `V` |

Hotkeys work only while recording and with no dialog open. Edit the palette,
hotkeys, HRV windows and ID patterns in `h10rig/config.py`.

### ID auto-fill

Attaching a document fills any empty ID field and marks it "From <file>":

- CSV/TXT: a header row plus first data row, or rows holding an ID field name
  followed by its value. The survey's baseline export (`Section, Field,
  Question, Answer`) works as is.
- JSON: every key at any depth (`participant_id`, `session_code`, ...).
- PDF: text and compressed (FlateDecode) content streams.
- Always the file name, e.g. `consent_PT-025_S-2026-019.pdf`.

ID patterns: `PT-025`, `S-2026-019`, `FC-03`. Files that disagree on an ID
produce a warning in the dialog; they do not block.

## Signal definitions

- **Beats** come from the H10's RR intervals (1/1024 s → ms) and run on a
  cumulative RR clock, resynced to arrival time if it drifts over 2 s.
- **Artifact**: RR more than 20 % from the median of the last 7 good RRs
  (once 5 are known). Kept and flagged; excluded from HR means and RMSSD.
- **Heart rate** shown: mean of the last 8 good beats. Chart: 5-beat moving
  average.
- **RMSSD**: successive differences of consecutive good beats under 2.5 s
  apart; needs 5 pairs. Rolling windows 15 s (default), 30 s, 45 s, all
  computed once a second.
- **% change** is against the **opening Silence** (recording start to the
  first instrument), once it holds 30 good beats. HR down and HRV up show
  green.
- **Signal**: artifact share over the last 60 s (Good < 2 %, Fair < 5 %,
  else Poor); "No beats" after 4 s without one.
- **ECG**: PMD stream, 130 Hz, microvolts, stamped on arrival. The sweep
  display buffers ~0.7 s to smooth BLE bursts; recording does not depend on it.

## Exports

Files are named `<session>_<participant>…`.

| Export | Contents |
| --- | --- |
| Beats CSV | `t_s, wallclock, rr_ms, hr_bpm, artifact, labels, participant_id, session_id, facilitator_id` (labels joined with `|`) |
| ECG CSV | `t_s, ecg_uv, labels` |
| JSON | `format: "sonorium-session/4"`: IDs, start, duration, documents with SHA-256, reference, label intervals with stats, rolling RMSSD for 15/30/45 s, events, beats |
| Session package (.zip) | `beats.csv`, `ecg.csv`, `session.json`, attached files under `consent/` and `questionnaire/` |

## Project layout

```
main.py                 entry point
h10rig/
  config.py             label palette, hotkeys, HRV windows, ID patterns
  ble.py                Polar H10: bleak connection, HR and ECG packet parsing
  hrv.py                artifact, RMSSD, signal quality (pure functions)
  session.py            session store: beats, ECG, label events, phases
  documents.py          attached documents, SHA-256, ID extraction
  export.py             CSV, JSON and zip exports
  registry.py           study registry used by the survey app in svaram-data
  ui/
    window.py           main window, frame loop, recording flow
    title_bar.py        logo, session IDs, sensor and recording controls
    vitals.py           HR, RMSSD, change, signal and beat tiles
    trend_chart.py      HR/HRV trend with label lanes
    ecg_sweep.py        monitor-style ECG sweep on ECG paper
    label_rail.py       label palette
    new_recording.py    documents and IDs dialog
    review.py           review table and exports
    widgets.py          painted buttons, pills, segmented control
    theme.py            light/dark colour tokens and stylesheet
tests/                  pytest
```

Only `h10rig/ble.py`, `ui/` and the entry point touch Bluetooth or Qt; the
rest is plain Python and covered by `pytest tests -q`.

## Notes

- A session lives in memory until it is exported. Save the package before
  quitting.
- `sessions/` holds recordings from the v0.2 recorder. They are kept as they
  are; the v0.2 source that reads them is archived in `archive/`.
