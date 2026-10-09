# MVP validation · 5 October 2026

## Completed automated checks

36 tests pass in version 0.2. The original 26 tests cover:

- Signed 24-bit ECG boundaries and 8/16/24-bit raw acceleration frames.
- HR flags, 8/16-bit heart rate, energy, contact and original RR ticks.
- Truncated/unsupported frames, PMD settings, command rejection and multipart acknowledgments.
- Shared sensor/host clock anchors and missing-sample gaps without interpolation.
- Atomic chunk publication, checksums, immutable raw files and unfinished temporary files.
- Missing, duplicate and corrupt chunks; unaffected chunks remain readable.
- A separate process forced to exit without cleanup; prior committed samples survive and recover.
- Committed chunks left out of a manifest by a crash; reconciliation on recovery.
- Export contents, valid CSV and refusal to export an active session.
- Versioned, repeatable respiration regeneration without raw-data mutation.
- A synthetic 15 breaths/min amplitude-modulated ECG fixture, gap rejection and flatline rejection. This is a software fixture check, not physiological validation.
- An accelerated 60-minute recording of 468,000 ECG samples and 180,000 motion samples: 1,440 independently verified chunks; empty pending buffers after commits; peak tracked Python allocation below 25 MB.
- Recording during simulated dropout and reconnection, disk-full interruption and reconnect retries.

## Desktop workflow checks

The native Qt window was exercised through simulator connection, start, recording, a full 20-second dropout, reconnect, stop/save, review, verification and ZIP/CSV export. The stored ECG has an approximately 20-second gap and no fabricated filler samples. All committed chunks verified.

Display updates were deliberately paused for over five seconds during recording; chunks continued to save independently. Screenshots of the recording, dropout and session-library views were checked for layout issues. The bundled executable was launched with its own embedded Python/runtime in an offline sandbox, and its macOS ad-hoc code signature verified.

## Remaining hardware acceptance

No live Polar H10 hardware recording was performed during this build. Before using the rig for a study, verify with the actual H10/firmware: discovery and macOS Bluetooth permission, negotiated ECG/ACC streams, contact flags and RR availability, battery reading, a physical 20-second out-of-range dropout/reconnect, an hour-long acquisition, and recovery after a forced quit. Compare the respiration estimator with a respiratory reference if the estimate will be interpreted scientifically.

The app is locally ad-hoc signed, not Apple-notarized for distribution to other Macs. This build targets Apple Silicon/macOS 14+. The app has no update/download path and requires no Internet at runtime.

## Version 0.2 dashboard validation

Ten additional tests check RR formulas against known values; excluded-observation and gap handling; selection boundaries; interval-union coverage; read-only session loading; corrupt-chunk isolation; active-session refusal; invalid respiration pointers; empty sessions; and rejected respiration windows.

The dashboard was exercised against the user's completed H10 sample recording. All five views, selection controls, ECG navigation, small-window layout and return-to-library navigation passed. Checksums of all 165 source files before/after analysis matched. Screenshots of each view were inspected. These checks validate the software behavior; they do not establish physiological or clinical accuracy.
