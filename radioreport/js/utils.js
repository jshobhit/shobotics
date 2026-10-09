/**
 * utils.js
 * Small shared helpers used by both the UI (app.js) and the PDF renderer.
 */
(function (global) {
  "use strict";

  /** "2026-06-16" -> "16 Jun 2026". Falls back to the raw string if unparsable. */
  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }

  function formatDateTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) +
      " · " +
      d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  }

  /** Make a string safe to use as a filename across platforms. */
  function sanitizeFilename(str) {
    return (str || "untitled")
      .trim()
      .replace(/[^a-z0-9\-_ ]/gi, "")
      .replace(/\s+/g, "_")
      .slice(0, 60) || "untitled";
  }

  function debounce(fn, wait) {
    let t;
    return function debounced(...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  /** "Region" + "Modality" -> "Region Modality" (e.g. "Spine MRI"). Skips missing parts. */
  function formatCaseType(c) {
    return [c && c.region, c && c.caseType].filter(Boolean).join(" ");
  }

  /**
   * Derive up to 3 monogram letters from a facility name for the placeholder
   * logo badge, e.g. "Rohini Health Clinic" -> "RHC". Skips common filler
   * words so things like "Diagnostic Centre of Excellence" still read sensibly.
   */
  function getInitials(name) {
    const FILLER = new Set(["of", "and", "the", "for", "&"]);
    const words = (name || "").trim().split(/\s+/).filter(Boolean);
    const letters = words
      .filter((w) => !FILLER.has(w.toLowerCase()))
      .map((w) => w[0].toUpperCase())
      .filter((ch) => /[A-Z]/.test(ch));
    return letters.slice(0, 3).join("") || "RHC";
  }

  global.Utils = { formatDate, formatDateTime, sanitizeFilename, debounce, formatCaseType, getInitials };
})(window);
