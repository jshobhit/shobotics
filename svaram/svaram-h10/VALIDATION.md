# Validation · v0.3 session desk · 10 October 2026

## Automated

`pytest tests -q`: 45 tests pass.

- HR packets: uint8 and uint16 values, energy field skipped, contact flags,
  RR ticks to ms, packets without RR.
- ECG frames: signed 24-bit samples including negative extremes; non-ECG
  frames ignored; the ECG start command bytes.
- Artifact detection (20 % of the 7-beat median, 5-beat minimum), RMSSD
  (known values, artifacts, gaps, 5-pair minimum), HR display mean, quality
  grades.
- Label rules (toggle, stack, Silence clears, empty is Silence), interval
  pairing, label tagging of beats and ECG, start/stop events, beat clock
  resync, 130 Hz ECG spacing, opening-silence reference and % change, no
  trimming of a stopped session.
- ID extraction: CSV header row, key/value rows, the survey baseline export,
  nested JSON, null values, file names, FlateDecode PDF with split TJ text.
- Exports: CSV columns, ECG labels, JSON shape, zip opens with every entry
  stored uncompressed, duplicate document names.
- Study registry and survey server (kept for the survey app).

## Desktop workflow (offscreen Qt, synthetic packets through the real handlers)

Empty states without a sensor; New recording gating; auto-fill from a CSV and
from a file name; conflict warning; user edit clears the auto-fill mark; Enter
starts; facilitator remembered; click and hotkey toggles; Silence when empty;
N ignored while recording; HRV window switch; sensor lost and reconnected
events; short press does not stop, a 1.2 s hold does; Review; all four
exports; the zip opens. No Qt warnings. Light, dark and narrow (< 960 px)
layouts were checked from screenshots.

Paint cost at 2x scale with a one-hour session: ECG sweep about 2.3 ms per
frame; trend 5.5 ms per repaint at the 5 min view, repainting a few times a
second (only when data arrives or time moves it by half a pixel).

## Not verified

No Polar H10 was available. Untested on hardware:

- Scanning, macOS Bluetooth permission prompt, connect and the HR
  notification subscription.
- The PMD ECG start sequence and frame sizes at the negotiated MTU, and
  "ECG stream unavailable" when it is refused.
- Disconnect detection, Reconnect reusing the same device, and the
  sensor-lost/reconnected timing in a real dropout.
- ECG sweep smoothness against real BLE packet timing.
- The macOS display wake lock (`caffeinate`) and Reduce motion detection.
- The PyInstaller build of this version.
