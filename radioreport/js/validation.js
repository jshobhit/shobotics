/**
 * validation.js
 * Form validation rules for case intake.
 */
(function (global) {
  "use strict";

  const ALLOWED_GENDERS = ["Female", "Male", "Other", "Prefer not to say"];
  const ALLOWED_CASE_TYPES = ["X-Ray", "CT", "MRI", "Ultrasound", "Mammography", "Other"];

  /**
   * Full validation — required before a case can move to preview/export.
   * @param {Object} data
   * @returns {{valid: boolean, errors: Object<string,string>}}
   */
  function validateFull(data) {
    const errors = {};

    if (!data.name || !data.name.trim()) {
      errors.name = "Patient name is required.";
    }

    const age = Number(data.age);
    if (data.age === "" || data.age === null || data.age === undefined) {
      errors.age = "Age is required.";
    } else if (!Number.isFinite(age) || age < 0 || age > 130 || !Number.isInteger(age)) {
      errors.age = "Enter a whole number between 0 and 130.";
    }

    if (!data.gender || !ALLOWED_GENDERS.includes(data.gender)) {
      errors.gender = "Select a gender.";
    }

    if (!data.caseType || !ALLOWED_CASE_TYPES.includes(data.caseType)) {
      errors.caseType = "Select a modality.";
    }

    if (!data.region || !data.region.trim()) {
      errors.region = "Enter the body region/anatomy.";
    }

    if (!data.examDate) {
      errors.examDate = "Exam date is required.";
    }

    if (!data.impression || !data.impression.trim()) {
      errors.impression = "Impression is required before export.";
    }

    return { valid: Object.keys(errors).length === 0, errors };
  }

  /**
   * Lenient validation — just enough to save a draft and come back later.
   */
  function validateDraft(data) {
    const errors = {};
    if (!data.name || !data.name.trim()) {
      errors.name = "Give the case a patient name so you can find it later.";
    }
    return { valid: Object.keys(errors).length === 0, errors };
  }

  global.Validation = {
    validateFull,
    validateDraft,
    ALLOWED_GENDERS,
    ALLOWED_CASE_TYPES,
  };
})(window);
