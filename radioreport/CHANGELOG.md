# Changelog

All notable changes to this project are recorded here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/), versioning follows [SemVer](https://semver.org/) (pre-1.0, so the minor version is where breaking changes currently land).

## [2.0.0] — 2026-07-02

Full visual + interaction overhaul to the **RadioReport v2** design (imported from a Claude Design document). The suite is now a guided, screen-by-screen flow rather than a single scrolling form, wrapped in a dark editorial theme. The underlying data model, storage, dictation engine, and PDF export are carried over from v0.2.0.

### Added
- **Five-screen guided flow**: New Case → Dictation → Processing → Review → Export, with a top-bar back button and per-step progress dots. A **Cases dashboard** (search + status badges) and **Letterhead settings** are retained from the previous version and restyled into the new theme.
- **Full-screen dictation experience** (`Dictation.createSession` in `js/dictation.js`): a single large record/pause control, live timer, animated waveform, and a running verbatim transcript. Same Web Speech engine as before — no API key, no network call by this app, only final results, verbatim. The per-textarea `attach()` API is kept for reuse.
- **Review screen** with source-tagged, click-to-edit blocks (DICTATED / TEMPLATE / AI) grouped into Protocol / Findings / Impression. The real dictated transcript populates the primary findings block; template and impression blocks seed from a fixed structured template you edit in place.
- **Self-hosted webfonts** (`css/fonts.css` + `assets/fonts/`): Cormorant Garamond, Inter, and IBM Plex Mono, extracted from the design bundle and served locally so the app still makes **zero network calls**.
- Export screen offers **Export PDF** (real jsPDF, as before), **Copy text** (plain-text report to clipboard), and a **Send to PACS** placeholder (UI only — no PACS integration exists).

### Changed
- Theme replaced end to end: dark `#0a0810` canvas with a purple accent (`#c94cff`), serif display headings, and mono labels — matching the v2 design 1:1.
- `js/app.js` rewritten from a multi-view form controller into a small state machine mirroring the design's logic, still vanilla and buildless.

### Known limitations
- **The "Processing / AI parse" step is a visual transition, not real AI.** There is no backend and no model call; the dictated transcript is surfaced in Review and the structured template blocks are editable seed text. Advisory flags shown in the design (e.g. "laterality corrected", "AI-synthesized · confidence 0.71") only appear while a block still holds its unedited seed default, so real content never carries a fabricated clinical warning. See README → Roadmap.
- Voice dictation still requires Chrome or Edge (Web Speech API).

## [0.2.0] — 2026-06-17

### Added
- Case type split into two fields: **Region** (free-text anatomy, e.g. "Spine", "Tongue / Oral cavity", with a 17-entry preset datalist) and **Modality** (the original case-type select). Dashboard table, search, and the PDF/preview patient grid all reflect both fields (e.g. "Spine MRI" instead of just "MRI").
- Voice dictation (`js/dictation.js`, new) on all four long-text fields — Clinical history, Technique & comparison, Findings, and Impression (previously a disabled placeholder on Impression only). Uses the browser-native Web Speech API: no API key, no network call made by this app, nothing sent to Anthropic/OpenAI. Only **final** recognition results are inserted, verbatim, at the cursor — no rewriting, punctuation cleanup, or summarization. Degrades to a disabled button with an explanatory tooltip in browsers without `SpeechRecognition` support (e.g. Firefox).
- Default letterhead branding: "Rohini Health Clinic" ships as the default facility name (still editable in Settings), with a dynamically generated monogram logo (clinic initials in a colored circle) shown on-screen in the letterhead and rendered as vector shapes (not a raster image) in the exported PDF.

### Changed
- Visual redesign: new teal-forward color palette, serif accents (facility name, view headings) via system fonts only (no external font loading — offline guarantee preserved), refreshed spacing/radii, and an animated "listening" state on dictation buttons.
- Exported PDF redesigned to match: vector logo badge in the letterhead, a "RADIOLOGY REPORT" pill tag, accent-tick section headings, and a 3-column × 2-row patient info grid (Patient/Age/Gender, then Region/Modality/Exam date) mirroring the on-screen preview layout.

### Known limitations
- Voice dictation requires Chrome or Edge (or another browser implementing the Web Speech API); Firefox and most Safari versions don't support it and will see a disabled button instead.
- Region is free text with presets, not a controlled vocabulary — no validation against a fixed anatomy list.

## [0.1.1] — 2026-06-16

### Fixed
- Settings (letterhead) modal rendered on top of the dashboard on first load and could not be dismissed via Cancel, the close (×) button, or clicking the backdrop. Root cause: `.modal-overlay { display: flex }` in `styles.css` overrode the `hidden` attribute -- author CSS beats the user-agent stylesheet's `[hidden] { display: none }` regardless of selector specificity, so toggling `el.hidden` in JS had no visual effect. The same bug affected the dashboard's "No cases yet" empty state, which could render even when cases existed.
- Added a single `[hidden] { display: none !important; }` rule so any element relying on the `hidden` attribute is reliably hidden going forward, regardless of what `display` value its class sets.

## [0.1.0] — 2026-06-16

Initial manual data-entry version. No OpenAI/Whisper integration in this release — see README → Roadmap.

### Added
- Case dashboard: list, search by name/case type, status badges (Draft / Exported), edit/preview/delete actions.
- Case form: patient information (name, age, gender, case type, exam date), clinical history, technique & comparison, findings, impression, recommendation.
- Draft save (lenient validation) vs. Save & Preview (full validation before a case can be exported).
- Preview screen with inline click-to-edit on every field except exam date.
- PDF export via vendored jsPDF — text-based layout (letterhead, patient grid, sections, signature block, page numbers), not a screenshot.
- Letterhead settings (facility name, radiologist name & credentials), persisted and reused across reports.
- All data stored in browser `localStorage` only — no network calls.
- Disabled "Dictate" button on the Impression field as a placeholder for the upcoming Whisper integration.

### Known limitations
- Case type does not currently drive dynamic per-modality fields (flat form, by design for this milestone).
- No data export/import — cases are tied to one browser profile.
- No automated test suite; verified via a manual jsdom-based smoke test covering the full add → edit → preview → export → delete flow (not checked into the repo).
