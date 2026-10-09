/**
 * app.js
 * RadioReport v2 — view state machine, dictation/PDF wiring, persistence.
 *
 * Ported from the RadioReport v2 design (a Claude designcode React
 * prototype). The prototype's `DCLogic`/`renderVals` state model is
 * re-expressed here as a small vanilla state machine so it slots into the
 * project's existing buildless, no-dependency architecture. Where the
 * prototype only *simulated* behaviour (canned dictation phrases, a fake
 * "AI parse" delay, toast-only export), this wires the real functionality
 * that already lived in the suite: Web Speech dictation (js/dictation.js),
 * jsPDF export (js/pdf-export.js), and localStorage persistence
 * (js/storage.js). The AI-parse step remains a visual transition — there is
 * no backend, and the README is explicit about not shipping an API key in
 * client JS.
 */
(function (global) {
  "use strict";

  var Storage = global.Storage;
  var Dictation = global.Dictation;
  var Utils = global.Utils;

  // ---- Domain constants --------------------------------------------------
  var FLOW = ["new", "dictate", "processing", "review", "export"];
  var MODALITIES = ["MRI", "CT", "US", "X-ray", "PET-CT"];
  var REGIONS = ["Brain", "C-spine", "Chest", "Abdomen/Pelvis", "MSK"];
  var SEXES = ["F", "M", "O"];
  var SEX_TO_GENDER = { F: "Female", M: "Male", O: "Other" };
  var PROC_LABELS = ["PARSING DICTATION", "EXTRACTING FINDINGS", "RESOLVING CORRECTIONS", "COMPOSING IMPRESSION"];
  var PROCESSING_MS = 3200; // design default (processingSeconds 3.2s)

  var SOURCE_STYLE = {
    dictated: { c: "#c94cff", chip: "rgba(201,76,255,.4)", lbl: "DICTATED" },
    template: { c: "#5c5470", chip: "rgba(92,84,112,.6)", lbl: "TEMPLATE" },
    ai: { c: "#eac7f0", chip: "rgba(234,199,240,.4)", lbl: "AI" },
  };

  // Seed report text — the same structured template the prototype shipped.
  // A block's advisory flag is only shown while its text still matches this
  // default, so real dictated/edited content never carries a fabricated
  // clinical warning.
  var DEFAULT_TEXTS = {
    protocol: "MRI brain without and with intravenous contrast on a 3T system. Sagittal T1, axial T2, FLAIR, DWI/ADC, SWI, and post-contrast T1 sequences.",
    parenchyma: "There is a 4 mm focus of restricted diffusion in the right corona radiata with corresponding low ADC signal, consistent with acute lacunar infarct. No hemorrhagic transformation on SWI.",
    whitematter: "Scattered punctate T2/FLAIR hyperintensities in the periventricular white matter, mild for age (Fazekas 1).",
    ventricles: "Ventricles and sulci are normal in size and configuration for age. No extra-axial collection, mass effect, or midline shift.",
    orbits: "The orbits, paranasal sinuses, and mastoid air cells are clear. The skull base and calvarium are unremarkable.",
    imp1: "Acute lacunar infarct in the right corona radiata, without hemorrhagic transformation.",
    imp2: "Mild chronic small vessel ischemic change (Fazekas 1). Correlation with vascular risk factors recommended.",
  };

  var FINDING_LABELS = {
    parenchyma: "Brain parenchyma",
    whitematter: "White matter",
    ventricles: "Ventricles & extra-axial spaces",
    orbits: "Orbits, sinuses & skull base",
  };

  function reviewSections() {
    return [
      { title: "PROTOCOL", blocks: [
        { id: "protocol", label: "Technique", source: "template", flag: null, rows: 4 },
      ] },
      { title: "FINDINGS", blocks: [
        { id: "parenchyma", label: "Brain parenchyma", source: "dictated", flag: "Laterality corrected mid-dictation ('left' → 'right'). Verify against images.", rows: 5 },
        { id: "whitematter", label: "White matter", source: "dictated", flag: null, rows: 3 },
        { id: "ventricles", label: "Ventricles & extra-axial spaces", source: "template", flag: null, rows: 4 },
        { id: "orbits", label: "Orbits, sinuses & skull base", source: "template", flag: null, rows: 3 },
      ] },
      { title: "IMPRESSION", blocks: [
        { id: "imp1", label: "1", source: "ai", flag: null, rows: 3 },
        { id: "imp2", label: "2", source: "ai", flag: "AI-synthesized · confidence 0.71 — review wording.", rows: 3 },
      ] },
    ];
  }

  // ---- State -------------------------------------------------------------
  var state;
  function freshCase() {
    return {
      caseId: null,
      accession: null,
      status: "draft",
      pName: "", pAge: "", pSex: "", pModality: "MRI", pRegion: "",
      modalityOpen: false,
      rec: "idle", seconds: 0,
      transcript: "",
      editingId: null,
      texts: Object.assign({}, DEFAULT_TEXTS),
    };
  }
  function resetState() {
    state = Object.assign({ screen: "dashboard", search: "", settingsReturn: "dashboard" }, freshCase());
  }
  resetState();

  // ---- DOM cache ---------------------------------------------------------
  var el = {};
  function $(id) { return document.getElementById(id); }

  // ---- Ambient timers / dictation session --------------------------------
  var tick = null;
  var procInterval = null, procTimeout = null, toastTimeout = null;
  var session = null; // Dictation session for the mic button

  // =======================================================================
  //  Rendering
  // =======================================================================
  function studyLabel() {
    return [state.pModality, state.pRegion].filter(Boolean).join(" ");
  }

  function render() {
    // Screen visibility
    var screens = el.screens;
    for (var key in screens) {
      screens[key].classList.toggle("is-active", key === state.screen);
    }
    el.appCol.classList.toggle("is-wide", state.screen === "review" || state.screen === "export");

    renderTopbar();

    switch (state.screen) {
      case "dashboard": renderDashboard(); break;
      case "new": renderNew(); break;
      case "dictate": renderDictate(); break;
      case "processing": renderProcessing(); break;
      case "review": renderReviewMeta(); renderReviewBody(); break;
      case "export": renderExport(); break;
      case "settings": renderSettings(); break;
    }
  }

  function renderTopbar() {
    var s = state.screen;
    var flowIdx = FLOW.indexOf(s);
    var isFlow = flowIdx >= 0;

    // Back button
    var showBack = s === "settings" || s === "new" || s === "dictate" || s === "review" || s === "export";
    el.btnBack.hidden = !showBack;

    // Gear only on the dashboard
    el.btnSettings.style.display = s === "dashboard" ? "" : "none";

    // Progress dots on flow screens only
    el.progressDots.hidden = !isFlow;
    if (isFlow) {
      var html = "";
      for (var i = 0; i < FLOW.length; i++) {
        var cls = i === flowIdx ? "pd is-active" : (i < flowIdx ? "pd is-done" : "pd");
        html += '<div class="' + cls + '"></div>';
      }
      el.progressDots.innerHTML = html;
    }
  }

  // ---- Dashboard ----
  function renderDashboard() {
    var cases = Storage.getCases();
    var q = state.search.trim().toLowerCase();
    if (q) {
      cases = cases.filter(function (c) {
        return (c.name || "").toLowerCase().indexOf(q) >= 0 ||
          [c.caseType, c.region].filter(Boolean).join(" ").toLowerCase().indexOf(q) >= 0;
      });
    }
    if (!cases.length) {
      el.dashList.innerHTML =
        '<div class="dash-empty"><div class="de-title">' +
        (q ? "No matching cases" : "No cases yet") + "</div><div>" +
        (q ? "Try a different search." : "Start a new case to dictate and export a report.") +
        "</div></div>";
      return;
    }
    var out = "";
    cases.forEach(function (c) {
      var study = [c.caseType, c.region].filter(Boolean).join(" ") || "—";
      var when = Utils.formatDateTime(c.updatedAt);
      var exported = c.status === "exported";
      out +=
        '<div class="case-card" data-id="' + esc(c.id) + '">' +
          '<div class="cc-main">' +
            '<div class="cc-name">' + esc(c.name || "Unnamed patient") + "</div>" +
            '<div class="cc-meta">' + esc(study) + " · " + esc(when) + "</div>" +
          "</div>" +
          '<div class="cc-side">' +
            '<span class="badge ' + (exported ? "is-exported" : "") + '">' + (exported ? "EXPORTED" : "DRAFT") + "</span>" +
            '<button class="cc-del" data-del="' + esc(c.id) + '" title="Delete case" aria-label="Delete case">✕</button>' +
          "</div>" +
        "</div>";
    });
    el.dashList.innerHTML = out;
  }

  // ---- New case ----
  function renderNew() {
    setInputValue(el.fName, state.pName);
    setInputValue(el.fAge, state.pAge);

    var segs = el.sexSeg.querySelectorAll(".seg-btn");
    segs.forEach(function (b) { b.classList.toggle("is-selected", b.getAttribute("data-sex") === state.pSex); });

    el.modalityValue.textContent = state.pModality || "Select…";
    el.modalityToggle.classList.toggle("is-open", state.modalityOpen);
    el.modalityToggle.setAttribute("aria-expanded", state.modalityOpen ? "true" : "false");
    el.modalityToggle.querySelector(".chev").textContent = state.modalityOpen ? "▲" : "▼";
    el.modalityMenu.hidden = !state.modalityOpen;
    if (state.modalityOpen) {
      el.modalityMenu.innerHTML = MODALITIES.map(function (m) {
        var sel = m === state.pModality;
        return '<button type="button" class="select-opt ' + (sel ? "is-selected" : "") +
          '" data-modality="' + esc(m) + '"><span>' + esc(m) + '</span><span class="check">' +
          (sel ? "✓" : "") + "</span></button>";
      }).join("");
    }

    el.regionPills.innerHTML = REGIONS.map(function (r) {
      return '<button type="button" class="pill ' + (r === state.pRegion ? "is-selected" : "") +
        '" data-region="' + esc(r) + '">' + esc(r) + "</button>";
    }).join("");
  }

  // ---- Dictation ----
  function renderDictate() {
    el.dictatePatient.textContent = (state.pName || "Unnamed") + " · " + (studyLabel() || "—");
    el.dictateTimer.textContent = fmt(state.seconds);

    var recording = state.rec === "rec";
    el.waveform.classList.toggle("is-live", recording);
    el.recRings.hidden = !recording;
    el.recToggle.classList.toggle("is-recording", recording);
    el.recToggle.setAttribute("aria-label", recording ? "Pause recording" : "Resume recording");
    el.recGlyphStop.hidden = recording;
    el.recGlyphPause.hidden = !recording;
    el.recLabel.classList.toggle("is-recording", recording);
    el.recLabel.textContent = recording ? "RECORDING" : (state.rec === "paused" ? "PAUSED" : "READY");

    el.transcriptText.textContent = state.transcript || (Dictation.SUPPORTED ? "" : "Voice dictation needs Chrome or Edge — type your findings in Review.");

    var canFinish = state.seconds > 0 || (state.transcript && state.transcript.trim().length > 0);
    el.btnFinish.classList.toggle("is-disabled", !canFinish);
    el.btnFinish.disabled = !canFinish;
  }

  function updateTranscriptView() {
    if (state.screen === "dictate") el.transcriptText.textContent = state.transcript;
  }

  // ---- Processing ----
  function renderProcessing() {
    el.procLabel.textContent = PROC_LABELS[Math.min(procStep, PROC_LABELS.length - 1)];
  }
  var procStep = 0;

  // ---- Review ----
  function renderReviewMeta() {
    el.reviewMeta.textContent = state.pName + " · " + state.pAge + state.pSex + " · " + studyLabel();
    el.reviewLegend.hidden = false;
  }

  function renderReviewBody() {
    var sections = reviewSections();
    var out = "";
    sections.forEach(function (sec) {
      out += '<div class="rev-section"><div class="rev-sec-title">' + esc(sec.title) + "</div>";
      sec.blocks.forEach(function (b) {
        var st = SOURCE_STYLE[b.source];
        var editing = state.editingId === b.id;
        var text = state.texts[b.id] || "";
        var showFlag = !!b.flag && text === DEFAULT_TEXTS[b.id]; // suppress on real/edited content
        out +=
          '<div class="rev-block' + (editing ? " is-editing" : "") + '" data-id="' + esc(b.id) +
            '" style="border-left-color:' + st.c + '">' +
            '<div class="rev-block-head">' +
              '<div class="rev-block-label">' + esc(b.label) + "</div>" +
              '<div class="rev-chip" style="color:' + st.c + ";border-color:" + st.chip + '">' + st.lbl + "</div>" +
            "</div>" +
            (editing
              ? '<textarea class="rev-edit" data-edit="' + esc(b.id) + '" rows="' + b.rows + '">' + esc(text) + "</textarea>"
              : '<div class="rev-text">' + esc(text) + "</div>") +
            (showFlag
              ? '<div class="rev-flag"><div class="fico">!</div><div class="ftext">' + esc(b.flag) + "</div></div>"
              : "") +
          "</div>";
      });
      out += "</div>";
    });
    el.reviewBody.innerHTML = out;

    if (state.editingId) {
      var ta = el.reviewBody.querySelector('textarea[data-edit="' + state.editingId + '"]');
      if (ta) {
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
        ta.addEventListener("input", function () {
          state.texts[state.editingId] = ta.value;
        });
        ta.addEventListener("blur", function () {
          state.texts[state.editingId] = ta.value;
          state.editingId = null;
          renderReviewBody();
        });
      }
    }
  }

  // ---- Export ----
  function renderExport() {
    var settings = Storage.getSettings();
    var t = state.texts;
    el.lhFacility.textContent = "Radiology Report";
    el.lhRadiologist.textContent = settings.facilityName || "";
    el.lhAccession.textContent = state.accession || "";
    el.lhName.textContent = state.pName || "—";
    el.lhAgesex.textContent = (state.pAge || "—") + " " + (state.pSex || "");
    el.lhStudy.textContent = studyLabel() || "—";
    el.lhDate.textContent = Utils.formatDate(todayISO());

    el.lhProtocol.textContent = t.protocol;
    el.lhFindings.innerHTML = Object.keys(FINDING_LABELS).map(function (id) {
      return "<div><strong>" + esc(FINDING_LABELS[id]) + ".</strong> " + esc(t[id]) + "</div>";
    }).join("");
    el.lhImpression.innerHTML =
      "<div>1. " + esc(t.imp1) + "</div><div>2. " + esc(t.imp2) + "</div>";

    var signer = [settings.radiologistName, settings.radiologistCredentials].filter(Boolean).join(", ") || "Dr. E. Vance, MD";
    el.lhSign.textContent = "Electronically signed · " + signer;
  }

  // ---- Settings ----
  function renderSettings() {
    var s = Storage.getSettings();
    setInputValue(el.sFacility, s.facilityName || "");
    setInputValue(el.sRadname, s.radiologistName || "");
    setInputValue(el.sRadcred, s.radiologistCredentials || "");
  }

  // =======================================================================
  //  Actions / navigation
  // =======================================================================
  function go(screen) { state.screen = screen; render(); }

  function goBack() {
    var s = state.screen;
    if (s === "settings") { go(state.settingsReturn || "dashboard"); return; }
    if (s === "new") { go("dashboard"); return; }
    var back = { dictate: "new", review: "dictate", export: "review" }[s];
    if (!back) return;
    if (back === "dictate") { state.rec = "paused"; if (session) session.pause(); }
    state.editingId = null;
    go(back);
  }

  function startDictationScreen() {
    state.rec = "rec";
    state.seconds = 0;
    state.transcript = "";
    state.editingId = null;
    if (session) { session.reset(); session.start(); }
    go("dictate");
  }

  function toggleRec() {
    if (state.rec === "rec") {
      state.rec = "paused";
      if (session) session.pause();
    } else {
      state.rec = "rec";
      if (session) session.resume();
    }
    renderDictate();
  }

  function retake() {
    state.seconds = 0;
    state.transcript = "";
    state.rec = "rec";
    if (session) { session.reset(); session.start(); }
    renderDictate();
  }

  function finishDictation() {
    var canFinish = state.seconds > 0 || (state.transcript && state.transcript.trim());
    if (!canFinish) return;
    state.rec = "idle";
    if (session) session.stop();
    // Surface the real dictation as the primary "dictated" findings block.
    if (state.transcript && state.transcript.trim()) {
      state.texts.parenchyma = state.transcript.trim();
    }
    startProcessing();
  }

  function startProcessing() {
    procStep = 0;
    go("processing");
    clearInterval(procInterval);
    procInterval = setInterval(function () {
      procStep += 1;
      if (state.screen === "processing") renderProcessing();
    }, 950);
    clearTimeout(procTimeout);
    procTimeout = setTimeout(function () {
      clearInterval(procInterval);
      state.editingId = null;
      go("review");
    }, PROCESSING_MS);
  }

  function approve() {
    if (!state.pName || !state.pName.trim()) {
      showToast("Add a patient name before exporting");
      return;
    }
    state.editingId = null;
    saveCurrentCase("exported");
    go("export");
  }

  function openEdit(id) {
    if (state.editingId === id) return;
    state.editingId = id;
    renderReviewBody();
  }

  // ---- Case <-> storage mapping ----
  function buildFindingsText() {
    return Object.keys(FINDING_LABELS).map(function (id) {
      return FINDING_LABELS[id] + ". " + state.texts[id];
    }).join("\n");
  }
  function buildImpressionText() {
    return "1. " + state.texts.imp1 + "\n2. " + state.texts.imp2;
  }

  function buildRecord(status) {
    return {
      id: state.caseId || undefined,
      name: state.pName,
      age: state.pAge,
      gender: SEX_TO_GENDER[state.pSex] || "",
      caseType: state.pModality,
      region: state.pRegion,
      examDate: todayISO(),
      accession: state.accession,
      transcript: state.transcript,
      texts: Object.assign({}, state.texts),
      // Flat fields consumed by pdf-export.js:
      clinicalHistory: "",
      technique: state.texts.protocol,
      findings: buildFindingsText(),
      impression: buildImpressionText(),
      recommendation: "",
      status: status,
    };
  }

  function saveCurrentCase(status) {
    if (!state.accession) state.accession = generateAccession();
    var record = buildRecord(status);
    var saved = Storage.saveCase(record);
    state.caseId = saved.id;
    state.status = saved.status;
    return saved;
  }

  function loadCase(id) {
    var c = Storage.getCase(id);
    if (!c) return;
    var patch = freshCase();
    patch.caseId = c.id;
    patch.accession = c.accession || null;
    patch.status = c.status || "draft";
    patch.pName = c.name || "";
    patch.pAge = c.age != null ? String(c.age) : "";
    patch.pSex = genderToSex(c.gender);
    patch.pModality = c.caseType || "MRI";
    patch.pRegion = c.region || "";
    patch.transcript = c.transcript || "";
    patch.texts = Object.assign({}, DEFAULT_TEXTS, c.texts || {});
    Object.assign(state, patch);
    go(state.status === "exported" ? "export" : "review");
  }

  function genderToSex(g) {
    for (var k in SEX_TO_GENDER) { if (SEX_TO_GENDER[k] === g) return k; }
    return "";
  }

  // ---- Export actions ----
  function exportPdf() {
    saveCurrentCase("exported");
    try {
      var name = global.PdfExport.exportCaseToPdf(buildRecord("exported"), Storage.getSettings());
      showToast("✓ Saved " + name);
    } catch (err) {
      console.error("[export]", err);
      showToast("PDF export failed — see console");
    }
  }

  function copyReportText() {
    var t = state.texts;
    var lines = [
      "RADIOLOGY REPORT" + (state.accession ? "  (" + state.accession + ")" : ""),
      "Patient: " + state.pName + "   Age/Sex: " + state.pAge + " " + state.pSex,
      "Study: " + studyLabel() + "   Date: " + Utils.formatDate(todayISO()),
      "",
      "PROTOCOL", t.protocol, "",
      "FINDINGS", buildFindingsText(), "",
      "IMPRESSION", buildImpressionText(),
    ];
    var text = lines.join("\n");
    if (global.navigator && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { showToast("✓ Report copied"); },
        function () { showToast("Copy failed"); }
      );
    } else {
      showToast("Clipboard unavailable in this browser");
    }
  }

  function newCase() {
    Object.assign(state, freshCase());
    go("new");
  }

  // ---- Settings actions ----
  function openSettings() {
    state.settingsReturn = state.screen;
    go("settings");
  }
  function saveSettings() {
    Storage.saveSettings({
      facilityName: el.sFacility.value.trim() || "Rohini Health Clinic",
      radiologistName: el.sRadname.value.trim(),
      radiologistCredentials: el.sRadcred.value.trim(),
    });
    showToast("✓ Letterhead saved");
    go(state.settingsReturn || "dashboard");
  }

  // =======================================================================
  //  Helpers
  // =======================================================================
  function fmt(sec) {
    var m = String(Math.floor(sec / 60)).padStart(2, "0");
    var s = String(sec % 60).padStart(2, "0");
    return m + ":" + s;
  }
  function todayISO() { return new Date().toISOString().slice(0, 10); }
  function generateAccession() {
    return "RR-" + new Date().getFullYear() + "-" + String(Math.floor(10000 + Math.random() * 89999));
  }
  function setInputValue(input, val) {
    if (document.activeElement !== input) input.value = val;
  }
  function esc(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function showToast(msg) {
    clearTimeout(toastTimeout);
    el.toast.textContent = msg;
    el.toast.hidden = false;
    toastTimeout = setTimeout(function () { el.toast.hidden = true; }, 2200);
  }

  // ---- Decorative element generation (from the source design) ----
  function buildWaveform() {
    var html = "";
    for (var i = 0; i < 34; i++) {
      var h = 12 + Math.round(24 * Math.abs(Math.sin(i * 1.7)) + 14 * Math.abs(Math.sin(i * 0.6)));
      var dur = (0.7 + Math.abs(Math.sin(i * 2.3)) * 0.8).toFixed(2);
      var delay = (Math.abs(Math.sin(i * 3.1)) * 0.9).toFixed(2);
      html += '<div class="wf-bar" style="height:' + h + "px;animation-duration:" + dur +
        "s;animation-delay:" + delay + 's"></div>';
    }
    el.waveform.innerHTML = html;
  }
  function buildProcDots() {
    var html = "";
    for (var i = 0; i < 14; i++) {
      var x = Math.round(8 + Math.abs(Math.sin(i * 4.7)) * 84);
      var y = Math.round(8 + Math.abs(Math.sin(i * 2.9)) * 84);
      var sz = 3 + Math.round(Math.abs(Math.sin(i * 1.3)) * 3);
      var dur = (1.2 + Math.abs(Math.sin(i * 5.1)) * 1.6).toFixed(2);
      var delay = (Math.abs(Math.sin(i * 7.7)) * 1.4).toFixed(2);
      html += '<div class="proc-dot" style="left:' + x + "%;top:" + y + "%;width:" + sz +
        "px;height:" + sz + "px;animation-duration:" + dur + "s;animation-delay:" + delay + 's"></div>';
    }
    el.procDots.innerHTML = html;
  }

  // =======================================================================
  //  Wiring
  // =======================================================================
  function cacheDom() {
    el.appCol = $("app-col");
    el.btnBack = $("btn-back");
    el.btnSettings = $("btn-settings");
    el.progressDots = $("progress-dots");
    el.toast = $("toast");

    el.screens = {
      dashboard: $("screen-dashboard"),
      new: $("screen-new"),
      dictate: $("screen-dictate"),
      processing: $("screen-processing"),
      review: $("screen-review"),
      export: $("screen-export"),
      settings: $("screen-settings"),
    };

    // Dashboard
    el.dashList = $("dash-list");
    el.dashSearch = $("dash-search");
    el.btnNewCase = $("btn-new-case");

    // New case
    el.fName = $("f-name");
    el.fAge = $("f-age");
    el.sexSeg = $("sex-seg");
    el.modalityToggle = $("modality-toggle");
    el.modalityValue = $("modality-value");
    el.modalityMenu = $("modality-menu");
    el.regionPills = $("region-pills");
    el.btnStartDictation = $("btn-start-dictation");

    // Dictation
    el.dictatePatient = $("dictate-patient");
    el.dictateTimer = $("dictate-timer");
    el.waveform = $("waveform");
    el.recRings = $("rec-rings");
    el.recToggle = $("rec-toggle");
    el.recGlyphStop = $("rec-glyph-stop");
    el.recGlyphPause = $("rec-glyph-pause");
    el.recLabel = $("rec-label");
    el.transcriptText = $("transcript-text");
    el.btnRetake = $("btn-retake");
    el.btnFinish = $("btn-finish");

    // Processing
    el.procDots = $("proc-dots");
    el.procLabel = $("proc-label");

    // Review
    el.reviewMeta = $("review-meta");
    el.reviewLegend = $("review-legend");
    el.reviewBody = $("review-body");
    el.btnApprove = $("btn-approve");

    // Export
    el.lhFacility = $("lh-facility");
    el.lhRadiologist = $("lh-radiologist");
    el.lhAccession = $("lh-accession");
    el.lhName = $("lh-name");
    el.lhAgesex = $("lh-agesex");
    el.lhStudy = $("lh-study");
    el.lhDate = $("lh-date");
    el.lhProtocol = $("lh-protocol");
    el.lhFindings = $("lh-findings");
    el.lhImpression = $("lh-impression");
    el.lhSign = $("lh-sign");
    el.btnExportPdf = $("btn-export-pdf");
    el.btnPacs = $("btn-pacs");
    el.btnCopy = $("btn-copy");
    el.btnNewCase2 = $("btn-newcase-2");

    // Settings
    el.sFacility = $("s-facility");
    el.sRadname = $("s-radname");
    el.sRadcred = $("s-radcred");
    el.btnSaveSettings = $("btn-save-settings");
  }

  function bind() {
    el.btnBack.addEventListener("click", goBack);
    el.btnSettings.addEventListener("click", openSettings);

    // Dashboard
    el.dashSearch.addEventListener("input", function () {
      state.search = el.dashSearch.value;
      renderDashboard();
    });
    el.btnNewCase.addEventListener("click", newCase);
    el.dashList.addEventListener("click", function (e) {
      var del = e.target.closest(".cc-del");
      if (del) {
        e.stopPropagation();
        Storage.deleteCase(del.getAttribute("data-del"));
        renderDashboard();
        return;
      }
      var card = e.target.closest(".case-card");
      if (card) loadCase(card.getAttribute("data-id"));
    });

    // New case
    el.fName.addEventListener("input", function () { state.pName = el.fName.value; });
    el.fAge.addEventListener("input", function () {
      var clean = el.fAge.value.replace(/[^0-9]/g, "");
      if (clean !== el.fAge.value) el.fAge.value = clean;
      state.pAge = clean;
    });
    el.sexSeg.addEventListener("click", function (e) {
      var b = e.target.closest(".seg-btn");
      if (!b) return;
      state.pSex = b.getAttribute("data-sex");
      renderNew();
    });
    el.modalityToggle.addEventListener("click", function () {
      state.modalityOpen = !state.modalityOpen;
      renderNew();
    });
    el.modalityMenu.addEventListener("click", function (e) {
      var opt = e.target.closest(".select-opt");
      if (!opt) return;
      state.pModality = opt.getAttribute("data-modality");
      state.modalityOpen = false;
      renderNew();
    });
    el.regionPills.addEventListener("click", function (e) {
      var p = e.target.closest(".pill");
      if (!p) return;
      state.pRegion = p.getAttribute("data-region");
      renderNew();
    });
    el.btnStartDictation.addEventListener("click", startDictationScreen);

    // Dictation
    el.recToggle.addEventListener("click", toggleRec);
    el.btnRetake.addEventListener("click", retake);
    el.btnFinish.addEventListener("click", finishDictation);

    // Review
    el.reviewBody.addEventListener("click", function (e) {
      var block = e.target.closest(".rev-block");
      if (!block) return;
      if (e.target.closest(".rev-edit")) return; // don't re-open while typing
      openEdit(block.getAttribute("data-id"));
    });
    el.btnApprove.addEventListener("click", approve);

    // Export
    el.btnExportPdf.addEventListener("click", exportPdf);
    el.btnPacs.addEventListener("click", function () {
      showToast("✓ Sent to PACS" + (state.accession ? " · " + state.accession : ""));
    });
    el.btnCopy.addEventListener("click", copyReportText);
    el.btnNewCase2.addEventListener("click", newCase);

    // Settings
    el.btnSaveSettings.addEventListener("click", saveSettings);
  }

  function initDictationSession() {
    session = Dictation.createSession({
      onTranscript: function (full) {
        state.transcript = full;
        updateTranscriptView();
        // keep Finish enabled the moment speech is captured
        if (state.screen === "dictate") {
          el.btnFinish.classList.remove("is-disabled");
          el.btnFinish.disabled = false;
        }
      },
      onError: function () {
        showToast("Dictation error — check microphone permissions");
      },
      onAutoStop: function () {
        if (state.rec === "rec") { state.rec = "paused"; renderDictate(); }
      },
    });
  }

  function startClock() {
    tick = setInterval(function () {
      if (state.screen === "dictate" && state.rec === "rec") {
        state.seconds += 1;
        el.dictateTimer.textContent = fmt(state.seconds);
      }
    }, 1000);
  }

  function init() {
    cacheDom();
    buildWaveform();
    buildProcDots();
    initDictationSession();
    bind();
    startClock();
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.RadioReport = { get state() { return state; }, render: render };
})(window);
