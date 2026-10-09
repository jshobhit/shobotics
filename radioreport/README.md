# RadioReport — Radiology Reporting Suite

A local-first reporting workflow for a consultant radiologist, presented as a guided flow: start a case, dictate, review a structured draft, and export a PDF report. This is the **RadioReport v2** design (imported from a Claude Design document) built on the suite's existing local storage, voice dictation, and PDF export.

**Everything runs on this machine.** There is no backend, no OpenAI/Whisper integration, and no AI model call — see [Roadmap](#roadmap). Nothing here makes a network call: all data lives in the browser's `localStorage`, webfonts are self-hosted, and dictation audio is handled entirely by the browser's own speech engine (no API key, nothing sent to Anthropic/OpenAI by this app).

## Status

`v2.0.0` — guided five-screen flow (New Case → Dictation → Processing → Review → Export) with a cases dashboard and letterhead settings, verbatim voice dictation (Web Speech API), self-hosted webfonts, and text-based PDF export. The "Processing / AI parse" step is a **visual transition only** — there is no model call; see [Roadmap](#roadmap).

## Running it

No build step, no server-side code, no dependencies to install. It's plain HTML/CSS/JS.

```bash
cd reporting-suite
npm start          # serves the folder at http://localhost:8080
# or, without npm:
python3 -m http.server 8080
```

Then open `http://localhost:8080` in a modern browser (Chrome, Edge, Safari, Firefox). You can also just double-click `index.html` — everything except `localStorage` persistence will still work, but serving it over `http://localhost` is recommended so storage behaves consistently across sessions.

## User flow

1. **Cases dashboard** — list of all cases saved on this machine, with search and a status badge (Draft / Exported). Click a case to reopen it; **＋ New case** starts a fresh one.
2. **New case** — patient name, age, sex (F/M/O), modality (MRI/CT/US/X-ray/PET-CT), and region (Brain/C-spine/Chest/Abdomen-Pelvis/MSK). **Start Dictation** moves to the next screen.
3. **Dictation** — a single large record/pause control with a live timer, waveform, and a running verbatim transcript via the browser's built-in speech recognition (Chrome/Edge; on unsupported browsers the transcript area explains you can type in Review instead). Only final, as-spoken text is captured — no cleanup, punctuation correction, or summarization. **Retake** clears it; **Finish** moves on.
4. **Processing** — a brief animated "parsing" transition. This is a visual step only; there is no AI model call.
5. **Review** — the report as source-tagged, click-to-edit blocks (DICTATED / TEMPLATE / AI) under Protocol / Findings / Impression. Your dictated transcript populates the primary findings block; template and impression blocks seed from a fixed structured template you edit in place. **Approve & export** saves the case and continues.
6. **Export** — a letterhead-style preview, then **Export PDF** (text-based, not a screenshot; named `Report_<PatientName>_<ExamDate>.pdf`), **Copy text**, **Send to PACS** (placeholder — no PACS integration), or **New case**.

Letterhead details (facility name, radiologist name & credentials) are set via the gear icon on the dashboard and reused on every report.

## Project structure

```
reporting-suite/
├── index.html              Single-page app shell — the seven screens (dashboard, new,
│                           dictate, processing, review, export, settings)
├── css/
│   ├── styles.css          Design tokens + component styles (v2 dark theme)
│   └── fonts.css           Self-hosted @font-face rules (Cormorant Garamond / Inter / IBM Plex Mono)
├── assets/
│   └── fonts/              woff2 files extracted from the design bundle (no CDN)
├── js/
│   ├── utils.js            Date formatting, filename sanitizing, debounce
│   ├── storage.js          localStorage CRUD for cases + letterhead settings
│   ├── validation.js       Draft vs. full-submit validation rules
│   ├── pdf-export.js       jsPDF-based report layout (text, not a screenshot)
│   ├── dictation.js        Web Speech API wiring — attach() for textareas, createSession() for the mic screen
│   └── app.js              View state machine, dictation/PDF wiring, persistence
├── lib/
│   └── jspdf.umd.min.js    Vendored locally — no CDN dependency at runtime
├── package.json            Metadata only; no install step required
├── CHANGELOG.md
└── README.md
```

> The original design file this was imported from is kept alongside as `RadioReport v2 (standalone).html` (a self-contained Claude Design bundle) for reference; it is not part of the running app.

## Design decisions worth knowing about

- **The "Processing / AI parse" step is a visual transition, not real AI.** There is no backend and no model call. On Finish, the real dictated transcript is surfaced in the Review screen's primary findings block, and the remaining Protocol / Findings / Impression blocks seed from one fixed structured template that the radiologist edits in place. The source tags (DICTATED / TEMPLATE / AI) and advisory flags come from the imported design; a block's flag is shown **only while its text still equals the unedited seed default**, so real dictated or edited content never carries a fabricated clinical warning. `DEFAULT_TEXTS` / `reviewSections()` in `js/app.js` define this seed and structure.
- **Dictation is deliberately "dumb."** `js/dictation.js` only ever commits *final* SpeechRecognition results, exactly as the engine returns them — no auto-punctuation pass, no rewriting, no AI cleanup. The Dictation screen uses `Dictation.createSession()` (running transcript for the mic button); the original `Dictation.attach()` (splice-into-textarea) is retained for reuse. A denied-permission or no-mic error is terminal and does **not** auto-retry.
- **PDF is generated as real text**, not a rasterized screenshot. That keeps it small, sharp at any zoom, and searchable/selectable — closer to what a referring clinician expects from a clinical document. The v2 review blocks are mapped back to the flat `technique` / `findings` / `impression` fields `js/pdf-export.js` expects (see `buildRecord()` in `js/app.js`).
- **Webfonts are self-hosted.** Cormorant Garamond, Inter, and IBM Plex Mono were extracted from the design bundle into `assets/fonts/` with `@font-face` rules in `css/fonts.css`, so the app keeps its zero-network-call guarantee rather than pulling from Google Fonts.
- **No accounts, no sync, no cloud.** Data lives in this browser's `localStorage`; a case is saved on **Approve & export**. Clearing browser data or switching browsers/machines means losing access to saved cases — there's no export/import of the raw case data yet (PDF export is one-way).

## Roadmap (not built yet)

- **Whisper / AI-assisted dictation** — the current "Dictate" buttons use the browser's own Web Speech API: verbatim, no API key, nothing sent to OpenAI/Anthropic. A future version could add an opt-in AI cleanup pass (punctuation, structure, summarization) on top of the verbatim transcript, but that's a distinct, explicitly-chosen feature — not something this version does silently.
- **OpenAI API key handling** — this is a static HTML/CSS/JS app with no backend. An API key embedded in client-side JS is readable by anyone with browser dev tools open. Before wiring up any OpenAI calls, decide on one of: a small local backend/proxy that holds the key server-side, or accepting that this stays a single-operator local tool where that exposure is acceptable. Don't ship a version that ships the key to other users' browsers.
- **PHI handling once a network call is involved.** Right now patient name/age/gender, and dictated text, never leave the machine — Web Speech API processing happens via the browser vendor's own speech service, not this app's code, and this app makes no network calls of its own. The moment a future feature sends report text to OpenAI or another third party, that's PHI leaving local storage for a third-party API — worth checking that provider's data-handling terms (and a BAA if this is used in a US HIPAA context) before that switch gets flipped.
- **Custom instructions / template inference** — the brief mentioned parsing into "an inferred template." This version uses one fixed structured template (clinical history / technique & comparison / findings / impression / recommendation). If multiple report templates are needed, that's a deliberate follow-on design decision, not something to infer silently.
- **Case data export/import** (e.g. JSON backup) so cases aren't trapped in one browser profile.

## Browser support

Built and tested against current Chrome/Edge/Safari/Firefox. Uses `localStorage`, `contenteditable`, and ES2017+ syntax (template literals, arrow functions, optional chaining is avoided to keep compatibility broad) — no transpilation step exists, so very old browsers aren't a target.

**Voice dictation specifically needs Chrome or Edge.** It's built on `SpeechRecognition`/`webkitSpeechRecognition`, which Firefox doesn't implement and Safari supports inconsistently across versions. On unsupported browsers the Dictation screen's transcript area explains you can type directly in the Review screen instead — everything else in the app still works normally.
