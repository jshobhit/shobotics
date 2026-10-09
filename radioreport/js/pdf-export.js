/**
 * pdf-export.js
 * Renders a case as a structured radiology report PDF using jsPDF.
 * Text-based layout (not a screenshot) so the output stays crisp and
 * the text remains selectable/searchable in the final PDF. The clinic
 * "logo" is drawn as vector shapes (circle + initials), not an embedded
 * raster image, so it stays just as crisp and needs no asset file.
 */
(function (global) {
  "use strict";

  const PAGE = { width: 210, height: 297 }; // A4, mm
  const MARGIN = { top: 18, bottom: 18, left: 20, right: 20 };
  const CONTENT_WIDTH = PAGE.width - MARGIN.left - MARGIN.right;

  const INK = [30, 36, 33];
  const MUTED = [94, 107, 102];
  const ACCENT = [14, 110, 109];
  const ACCENT_SOFT = [227, 241, 240];
  const RULE = [230, 227, 218];

  function newDoc() {
    const { jsPDF } = global.jspdf;
    return new jsPDF({ unit: "mm", format: "a4" });
  }

  function checkPageBreak(doc, y, neededHeight) {
    if (y + neededHeight > PAGE.height - MARGIN.bottom) {
      doc.addPage();
      return MARGIN.top;
    }
    return y;
  }

  /** Small accent tick + label — gives section headings a tab-like feel. */
  function addSectionHeading(doc, y, text) {
    y = checkPageBreak(doc, y, 10);
    doc.setDrawColor(...ACCENT);
    doc.setLineWidth(1.1);
    doc.line(MARGIN.left, y - 3, MARGIN.left, y + 1);

    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(...ACCENT);
    if (typeof doc.setCharSpace === "function") doc.setCharSpace(0.4);
    doc.text(text.toUpperCase(), MARGIN.left + 3, y);
    if (typeof doc.setCharSpace === "function") doc.setCharSpace(0);
    return y + 5.5;
  }

  function addParagraph(doc, y, text) {
    doc.setFont("helvetica", "normal");
    doc.setFontSize(10.5);
    doc.setTextColor(...INK);
    const lines = doc.splitTextToSize(text || "—", CONTENT_WIDTH);
    const lineHeight = 5;
    for (const line of lines) {
      y = checkPageBreak(doc, y, lineHeight);
      doc.text(line, MARGIN.left, y);
      y += lineHeight;
    }
    return y + 4; // trailing space after the paragraph
  }

  function addSection(doc, y, title, body) {
    if (!body || !String(body).trim()) return y; // skip empty optional sections
    y = addSectionHeading(doc, y, title);
    y = addParagraph(doc, y, body);
    return y;
  }

  function addLabelValuePair(doc, x, y, label, value) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(7.5);
    doc.setTextColor(...MUTED);
    if (typeof doc.setCharSpace === "function") doc.setCharSpace(0.3);
    doc.text(label.toUpperCase(), x, y);
    if (typeof doc.setCharSpace === "function") doc.setCharSpace(0);

    doc.setFont("helvetica", "normal");
    doc.setFontSize(10.5);
    doc.setTextColor(...INK);
    doc.text(String(value || "—"), x, y + 4.6);
  }

  /** Vector placeholder logo: a filled circle with the facility's initials. */
  function addLogoBadge(doc, cx, cy, r, initials) {
    doc.setFillColor(...ACCENT);
    doc.circle(cx, cy, r, "F");
    doc.setDrawColor(255, 255, 255);
    doc.setLineWidth(0.3);
    doc.circle(cx, cy, r - 0.7, "S");
    doc.setTextColor(255, 255, 255);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(initials.length >= 3 ? 9 : 11);
    doc.text(initials, cx, cy + r * 0.33, { align: "center" });
  }

  /** Rounded pill used for the "RADIOLOGY REPORT" tag, top-right of the letterhead. */
  function addDocLabel(doc, rightX, y, text) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(8.5);
    if (typeof doc.setCharSpace === "function") doc.setCharSpace(0.5);
    const width = doc.getTextWidth(text) + 9;
    const height = 6.4;
    const x = rightX - width;
    const labelY = y - 4.6;

    doc.setFillColor(...ACCENT_SOFT);
    doc.setDrawColor(...ACCENT);
    doc.setLineWidth(0.3);
    doc.roundedRect(x, labelY, width, height, height / 2, height / 2, "FD");

    doc.setTextColor(...ACCENT);
    doc.text(text, x + width / 2, labelY + height / 2 + 1.5, { align: "center" });
    if (typeof doc.setCharSpace === "function") doc.setCharSpace(0);
  }

  /**
   * @param {Object} caseData - the case record (patient fields + report sections)
   * @param {Object} settings - { facilityName, radiologistName, radiologistCredentials }
   * @returns {jsPDF} the built document (caller decides whether to save/preview)
   */
  function buildReportPdf(caseData, settings) {
    const doc = newDoc();
    let y = MARGIN.top;

    // --- Letterhead -------------------------------------------------
    const initials = global.Utils.getInitials(settings.facilityName);
    const logoR = 6;
    addLogoBadge(doc, MARGIN.left + logoR, y + 2.3, logoR, initials);
    const textX = MARGIN.left + logoR * 2 + 4;

    doc.setFont("times", "bold");
    doc.setFontSize(15);
    doc.setTextColor(...INK);
    doc.text(settings.facilityName || "Facility name not set", textX, y);

    doc.setFont("helvetica", "normal");
    doc.setFontSize(9.5);
    doc.setTextColor(...MUTED);
    const radioLine = [settings.radiologistName, settings.radiologistCredentials]
      .filter(Boolean)
      .join(", ");
    doc.text(radioLine || "Reporting radiologist not set", textX, y + 5.6);

    addDocLabel(doc, PAGE.width - MARGIN.right, y, "RADIOLOGY REPORT");

    y += 11;
    doc.setDrawColor(...ACCENT);
    doc.setLineWidth(0.8);
    doc.line(MARGIN.left, y, PAGE.width - MARGIN.right, y);
    y += 9;

    // --- Patient info grid -------------------------------------------
    const colWidth = CONTENT_WIDTH / 3;
    addLabelValuePair(doc, MARGIN.left, y, "Patient", caseData.name);
    addLabelValuePair(doc, MARGIN.left + colWidth, y, "Age", caseData.age != null ? `${caseData.age} yrs` : "");
    addLabelValuePair(doc, MARGIN.left + colWidth * 2, y, "Gender", caseData.gender);
    y += 11;
    addLabelValuePair(doc, MARGIN.left, y, "Region", caseData.region);
    addLabelValuePair(doc, MARGIN.left + colWidth, y, "Modality", caseData.caseType);
    addLabelValuePair(doc, MARGIN.left + colWidth * 2, y, "Exam date", global.Utils.formatDate(caseData.examDate));
    y += 11;

    doc.setDrawColor(...RULE);
    doc.line(MARGIN.left, y, PAGE.width - MARGIN.right, y);
    y += 9;

    // --- Report sections ----------------------------------------------
    y = addSection(doc, y, "Clinical history", caseData.clinicalHistory);
    y = addSection(doc, y, "Technique & comparison", caseData.technique);
    y = addSection(doc, y, "Findings", caseData.findings);
    y = addSection(doc, y, "Impression", caseData.impression);
    y = addSection(doc, y, "Recommendation", caseData.recommendation);

    // --- Footer (signature block on the last page) --------------------
    y = checkPageBreak(doc, y, 20);
    y = Math.max(y, PAGE.height - MARGIN.bottom - 18);
    doc.setDrawColor(...RULE);
    doc.line(MARGIN.left, y, PAGE.width - MARGIN.right, y);
    y += 6;
    doc.setFont("helvetica", "bold");
    doc.setFontSize(9.5);
    doc.setTextColor(...INK);
    doc.text(radioLine || "Reporting radiologist not set", MARGIN.left, y);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(...MUTED);
    doc.text("Electronically prepared — manual entry version", MARGIN.left, y + 4.4);
    doc.text(
      `Generated ${global.Utils.formatDateTime(Date.now())}`,
      PAGE.width - MARGIN.right,
      y,
      { align: "right" }
    );

    // --- Page numbers ---------------------------------------------------
    const pageCount = doc.internal.getNumberOfPages();
    if (pageCount > 1) {
      for (let i = 1; i <= pageCount; i++) {
        doc.setPage(i);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(8);
        doc.setTextColor(...MUTED);
        doc.text(`Page ${i} of ${pageCount}`, PAGE.width / 2, PAGE.height - 9, { align: "center" });
      }
    }

    return doc;
  }

  function exportCaseToPdf(caseData, settings) {
    const doc = buildReportPdf(caseData, settings);
    const datePart = caseData.examDate || new Date().toISOString().slice(0, 10);
    const filename = `Report_${global.Utils.sanitizeFilename(caseData.name)}_${datePart}.pdf`;
    doc.save(filename);
    return filename;
  }

  global.PdfExport = { buildReportPdf, exportCaseToPdf };
})(window);
