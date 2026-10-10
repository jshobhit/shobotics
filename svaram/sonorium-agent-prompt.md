# Task: implement the Sonorium Session Desk redesign on a new branch

You are working inside the Sonorium codebase (the Shobotics session recording dashboard for instrumented sound-therapy sessions). Implement the redesigned application described below, verify it builds, and commit it to a new branch. Work in small, readable code. No speculative features, no new abstractions without a second caller, no new runtime dependencies.

A working single-file reference implementation is at `docs/reference/sonorium.html`. Read it fully before writing code. It is the source of truth for behaviour, layout, copy, colours and the Polar H10 protocol. Port its behaviour into this codebase's existing stack and conventions. Do not embed the HTML file into the app.

## 0. Preconditions and branch

1. Run `git status`. If the working tree is dirty, stop and report what is uncommitted. Do not stash or discard anything.
2. Identify the default branch (`git symbolic-ref refs/remotes/origin/HEAD`, else `main`, else `master`). `git fetch` and fast-forward it if a remote exists.
3. Create and switch to `feat/session-desk-redesign` from the default branch. If that name exists, stop and report.
4. Never push, open a PR, rewrite history or touch other branches unless asked.

## 1. Read before writing

- Identify the stack: framework, language, build tool, styling approach, package manager (use the lockfile that exists), lint, typecheck and test scripts in `package.json`.
- Map the current app: entry point, the dashboard screen, the Live HR / Live ECG / Active Sound Label cards, the Recording Dashboard form, the stats panel, the instrument grid, any Bluetooth code, any export code.
- Note what can be deleted. The redesign replaces the three "Connect" cards, the stacked form, and the bottom instrument grid.
- Write a 10-line plan (files to add, change, delete) in your scratch notes before editing. Keep to it.

## 2. What to build

### 2.1 Window layout (desktop first)
Fixed full-viewport app, no page scroll at 1280x720 and above:
- **Title bar (50px):** the Shobotics logo (use the existing `shobotics-logo.svg` asset; render it so its fill follows the theme's primary colour, e.g. inline SVG with `fill="currentColor"`), a divider, "Sonorium", the session IDs (`PT · S · FC`, monospace), a document count pill, then right-aligned: sensor pill, "Connect Polar H10" (hidden when connected), recording pill (Idle / Recording / Stopped), elapsed clock, and one of: "Hold to stop" (recording), "Review" (stopped), "New recording `N`" (idle or stopped).
- **Left column:** an alert strip (only when needed), a vitals strip, the HR/HRV chart (fills remaining height), the live ECG (fixed ~164px).
- **Right rail (300px):** "Active labels" palette, always visible, scrolls internally if short on height.
- Below 960px width: stack to one column and allow scrolling. No horizontal scroll at any width.
- System font stack for UI, `ui-monospace` stack for numbers. No web fonts. Light and dark themes from the reference's colour tokens.

### 2.2 Signal gating
- **No simulator, no demo data, no synthetic signals anywhere.** Remove any existing mock/fake data paths.
- Only a connected Polar H10 produces beats or ECG. Without one, charts show empty states: "Connect a Polar H10 to see live heart rate and HRV" and "Connect a Polar H10 to see the live ECG". Vitals show "–".
- Recording cannot start unless the H10 is connected.

### 2.3 Polar H10 over Web Bluetooth
Port exactly from the reference:
- `requestDevice({ filters: [{ namePrefix: "Polar H10" }], optionalServices: ["heart_rate", PMD_SERVICE] })`.
- Heart Rate Measurement notifications: parse flags (uint16 HR bit, sensor-contact bits, energy-expended skip, RR-present bit); RR values are 1/1024 s, convert to ms.
- Beat timestamps run on a cumulative RR clock, resynced to arrival time if they drift more than 2 s. Beats must stay sorted by time.
- PMD ECG: service `fb005c80-02e7-f387-1cad-8acd2d8df0c8`, control `...c81...`, data `...c82...`. Start notifications on control, then data, then write `02 00 00 01 82 00 01 01 0E 00` (ECG, 130 Hz, 14-bit). Data frames: byte 0 = 0x00, 10-byte header, then 3-byte little-endian signed samples in microvolts.
- If PMD fails, keep HR/RR running and show "ECG stream unavailable".
- On `gattserverdisconnected`: alert strip with Reconnect; during a recording, log a `signal` event ("Sensor lost") and keep the session running; reconnect reuses the same device.
- If `navigator.bluetooth` is missing, show: "Bluetooth isn't available here. Open the app in Chrome or Edge on the desktop."

### 2.4 Signal processing (pure functions, no framework imports)
- Artifact beat: RR more than 20 % from the median of the last 7 good RRs (needs at least 5). Artifacts are kept, flagged, excluded from HR means and RMSSD.
- HR display: mean of the last 8 good beats. Chart line: 5-beat moving average.
- RMSSD: successive differences of consecutive non-artifact beats less than 2.5 s apart; null under 5 pairs.
- **Rolling RMSSD window: 15 s by default (the minimum), user-selectable 15 / 30 / 45 s.** Compute all three series once per second so switching is instant. The selected window drives the vitals tile and the chart's RMSSD line.
- Use binary search on the sorted beat array for window slices. Do not filter the whole array per frame.
- **Reference for % change = the opening Silence period** (recording start until the first instrument). Needs at least 30 good beats. While it is still open, show "Opening silence in progress". HR change is good when negative, HRV change is good when positive.
- Signal quality: artifact % over the last 60 s (Good < 2 %, Fair < 5 %, else Poor); "No beats" if none for 4 s.

### 2.5 Labels
- Palette families and hotkeys (keep this data in one config array):
  - Control: Silence `0`
  - Gongs and metal: Plate Gong `1`, Plate Bells `2`, Tubular Bells `3`, Spiral Chime `4`, Tubular Resonators `5`, Angel Chimes `6`
  - Drums and rattles: Storm Drum `Q`, Shamanic Drum `W`, Ocean Drum `E`, Rattles `R`
  - Stone, glass, water: Sound Stone `A`, Glassophone `S`, Water Bowl `D`
  - Bed, breath, voice: Sound Bed `Z`, Flutes `X`, Wind Breath `C`, Natural Voice `V`
- **No Baseline label and no marker labels** (Movement, Cough, Speech, Strap adjusted are removed).
- Instruments toggle independently; several can be active at once. Pressing Silence clears all. **When nothing is active the active set is `{Silence}`**. A recording always starts in Silence.
- Every toggle logs `on` / `off` events with timestamps. Every beat stores the active label set at that moment.
- Active buttons show the label colour and their running time. The rail header shows "Silence" or "N active".
- Hotkeys work only while recording, no dialog is open, and focus is not in a text field. `N` opens New recording when not recording.
- Chart: one coloured lane per label seen in the visible window, under the RMSSD panel.

### 2.6 New recording dialog (minimal)
Native `<dialog>`, ~600px wide. Contents only:
- Two document drop zones side by side: **Consent form** (PDF or image) and **Questionnaire** (PDF, CSV, JSON, XLSX, TXT or image). Attach button, drag and drop, multiple files, remove.
- Three ID fields: Participant ID, Session ID, Facilitator ID (facilitator remembered in localStorage).
- Footer: one hint line listing what is missing, a "Connect Polar H10" button only if disconnected, and "Start recording `↵`".
- Start is enabled only with both documents, all three IDs, and a connected H10.
- **ID auto-fill on attach.** Fill only empty fields, mark them as auto-filled with "From <file>", clear that mark when the user edits:
  - CSV/TXT: header row + first data row, or `key,value` rows, where the key contains participant / session / facilitator and an id/code/number word.
  - JSON: walk all keys with the same rule.
  - PDF: scan raw bytes plus FlateDecode streams inflated with `DecompressionStream("deflate")` (trim CR/LF before `endstream`), joining split `TJ` fragments, then apply the ID regexes.
  - Always also scan the file name (underscores and dots treated as spaces).
  - Regexes in one config object: `PT-?\d{2,6}`, `S-\d[\d-]{1,12}\d`, `FC-?\d{1,4}`.
  - If two files disagree on an ID, show a warning in the hint ("IDs differ between files: ...") but do not block.
- Store each document's name, size, type, SHA-256 and attached time. On start, the dialog's documents become the session's documents and the dialog resets.

### 2.7 Live ECG sweep animation
Clinical monitor behaviour on ECG paper:
- Paper grid: 1 mm minor and 5 mm major lines at 25 mm/s and 10 mm/mV, sized so the strip shows 5 s.
- Incoming samples go into a jitter queue. Playout starts at ~90 queued samples and releases samples at exactly 130 Hz from the animation clock (fractional carry). If more than 2 s behind, catch up.
- A write head sweeps left to right over a 650-sample ring buffer. The new trace is drawn up to the head; the previous sweep is drawn faded beyond an 18-sample erase gap ahead of the head; the head has a small glowing dot.
- Recorded ECG samples are stored with their arrival timestamps, independent of playout.
- Respect `prefers-reduced-motion` only for decorative effects; the waveform itself must keep moving.

### 2.8 Recording control and review
- Stop is press-and-hold for 1.2 s (pointer and keyboard). On stop: close open label intervals, log "Recording stopped", open Review.
- Review dialog: duration, beats, artifact %, opening-silence reference; a table of label intervals (label, start, duration, mean HR, RMSSD, ΔHR %, ΔRMSSD %); exports.
- Warn on page unload while recording or with an unsaved stopped session. The New recording dialog warns that starting discards an unsaved session.
- Request a screen wake lock while recording; ignore failure.

### 2.9 Exports (file names `<session>_<participant>...`)
- **Beats CSV:** `t_s, wallclock, rr_ms, hr_bpm, artifact, labels, participant_id, session_id, facilitator_id` (labels joined with `|`).
- **ECG CSV:** `t_s, ecg_uv, labels`.
- **Session JSON** (`format: "sonorium-session/4"`): IDs, start time, duration, document metadata with SHA-256, reference stats, label intervals with stats, rolling RMSSD series for 15/30/45 s, events, beats.
- **Session package (.zip):** `beats.csv`, `ecg.csv`, `session.json`, and the attached files under `consent/` and `questionnaire/`. Use the reference's store-only zip writer (CRC32 table, local headers, central directory, UTF-8 flag). No zip library.
- Save with a Blob + `<a download>`.

## 3. Engineering constraints

- **Performance:** never put beats or ECG samples in reactive UI state. Keep signal buffers in plain module or ref storage. One `requestAnimationFrame` loop draws both canvases (devicePixelRatio aware, resize-safe). Vitals and rail timers update at 2 Hz. UI state is only phase, active labels, dialog contents and settings.
- **No new runtime dependencies.** No chart, zip, date, state or BLE libraries. Remove dependencies that become unused, and say which.
- **Structure (adapt names to the repo's conventions):**
  - `ble/polarH10` (connect, parse, events)
  - `signal/` (artifact, RMSSD, reference, interval maths; pure)
  - `session/` (labels and events, session store)
  - `export/` (CSV, JSON, zip)
  - `docs/extractIds`
  - UI components: TitleBar, Vitals, TrendChart, EcgSweep, LabelRail, NewRecordingDialog, ReviewDialog
- **Pure logic must be framework-free.** If the repo already has a test runner, add focused tests for: HR packet parsing (with and without RR, uint16 flag), ECG frame decoding (negative samples), artifact detection, RMSSD, nextSet label rules, intervals, ID extraction (CSV header, key/value CSV, nested JSON, filename, Flate PDF), and that the zip opens. If there is no test runner, do not add one; run these checks once with a throwaway script and delete it.
- Delete replaced components and dead code. Do not leave commented-out code.
- Match the repo's existing lint and format rules. TypeScript strict if the repo uses TS; no `any` on BLE/DataView code.
- Copy: plain, active, short. Use the reference's strings.

## 4. Desktop note
Web Bluetooth needs Chromium. Do not add a desktop wrapper in this branch.
- If the repo already uses Electron, wire `session.on("select-bluetooth-device")` to pick the first device whose name starts with "Polar H10" and grant `bluetooth` permission. Leave the rest alone.
- Do not introduce Tauri. Its system webviews do not support Web Bluetooth on macOS or Linux.

## 5. Verify

1. Install with the existing package manager. Run typecheck, lint, tests (if present) and the production build. All must pass. Fix what you broke; report pre-existing failures without fixing unrelated code.
2. Run the dev server and check in Chrome:
   - Empty states with no sensor.
   - New recording gating, ID auto-fill from a sample CSV and a filename, and the conflict warning.
   - Label toggling by click and hotkey, with Silence when empty.
   - HRV window switch.
   - Hold-to-stop, Review, and all four exports. The zip must open.
   - Dark mode.
   - No console errors.
3. You cannot test a real H10. Say so explicitly in the report and list the BLE paths that are untested.

## 6. Commit

- Commit on `feat/session-desk-redesign` in 2 to 4 focused commits, for example:
  1. `feat(signal): polar h10 ble, hrv and label logic`
  2. `feat(ui): session desk layout, label rail, ecg sweep`
  3. `feat(session): new recording dialog, review, exports`
  4. `chore: remove simulator, old cards and unused deps`
- Each commit must build. Conventional-commit subject lines, imperative, under 72 characters, with a body saying what and why.
- Do not commit build output, `.env` files, the reference HTML (unless it already lives in the repo), or anything from the user's sessions.

## 7. Report back (short)

- Branch name and commit list.
- Files added, changed and deleted.
- Dependencies removed.
- Checks run and their results.
- What was not verified (real H10 BLE, ECG timing on hardware).
- Any deviation from this spec, and why.
