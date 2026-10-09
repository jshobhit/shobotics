/**
 * storage.js
 * Thin wrapper around localStorage for cases + letterhead settings.
 * Everything stays on this machine — no network calls in this version.
 */
(function (global) {
  "use strict";

  const KEYS = {
    cases: "radreport.cases.v1",
    settings: "radreport.settings.v1",
  };

  function readJSON(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return fallback;
      const parsed = JSON.parse(raw);
      return parsed == null ? fallback : parsed;
    } catch (err) {
      console.error("[storage] failed to read", key, err);
      return fallback;
    }
  }

  function writeJSON(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (err) {
      console.error("[storage] failed to write", key, err);
      return false;
    }
  }

  function generateId() {
    return "case_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
  }

  const Storage = {
    /** @returns {Array<Object>} all cases, newest first */
    getCases() {
      const cases = readJSON(KEYS.cases, []);
      return cases.slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    },

    getCase(id) {
      return readJSON(KEYS.cases, []).find((c) => c.id === id) || null;
    },

    /** Upsert a case by id. Sets createdAt/updatedAt automatically. */
    saveCase(caseData) {
      const cases = readJSON(KEYS.cases, []);
      const now = Date.now();
      let record = { ...caseData };

      if (!record.id) {
        record.id = generateId();
        record.createdAt = now;
        record.status = record.status || "draft";
      }
      record.updatedAt = now;

      const idx = cases.findIndex((c) => c.id === record.id);
      if (idx >= 0) {
        cases[idx] = { ...cases[idx], ...record };
        record = cases[idx];
      } else {
        cases.push(record);
      }

      writeJSON(KEYS.cases, cases);
      return record;
    },

    deleteCase(id) {
      const cases = readJSON(KEYS.cases, []).filter((c) => c.id !== id);
      writeJSON(KEYS.cases, cases);
    },

    getSettings() {
      return readJSON(KEYS.settings, {
        facilityName: "Rohini Health Clinic",
        radiologistName: "",
        radiologistCredentials: "",
      });
    },

    saveSettings(settings) {
      writeJSON(KEYS.settings, settings);
      return settings;
    },

    generateId,
  };

  global.Storage = Storage;
})(window);
