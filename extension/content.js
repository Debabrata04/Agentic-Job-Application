// Fills form fields in this frame using data from the Job Agent backend.
// Matching is by label text, same labels the backend's own Playwright run
// used when generating filled_answers - so exact (normalized) match is the
// common case, with substring fallback for minor label drift.

function normalize(text) {
  return (text || "").toLowerCase().trim().replace(/\s+/g, " ").replace(/[*:]+$/, "").trim();
}

function getFieldLabel(field) {
  if (field.labels && field.labels.length) {
    const text = field.labels[0].innerText || field.labels[0].textContent;
    if (text && text.trim()) return text.trim();
  }
  const ariaLabel = field.getAttribute("aria-label");
  if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
  const ariaLabelledBy = field.getAttribute("aria-labelledby");
  if (ariaLabelledBy) {
    const el = document.getElementById(ariaLabelledBy);
    if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
  }
  const placeholder = field.getAttribute("placeholder");
  if (placeholder && placeholder.trim()) return placeholder.trim();
  // Some ATS platforms (confirmed on Lever) render the real question text as
  // a sibling "label" element inside a shared question container, with no
  // <label>, aria-label/labelledby, or placeholder on the field itself, and
  // the field nested a level or two below rather than directly adjacent to
  // the label - so the plain previousElementSibling walk below never finds
  // it either. Same fix as the backend's own Playwright filler
  // (application_filler.py) uses server-side: walk up to the nearest
  // question-like container and pull the first label-like descendant that
  // doesn't itself contain the field.
  const container = field.closest('[class*="question" i], fieldset') || field.closest("li");
  if (container) {
    const candidates = container.querySelectorAll('[class*="label" i]');
    for (const c of candidates) {
      if (c.contains(field)) continue;
      const clone = c.cloneNode(true);
      clone.querySelectorAll('[class*="required" i]').forEach((r) => r.remove());
      const text = (clone.innerText || clone.textContent || "").trim();
      if (text) return text;
    }
  }
  // Fall back to the nearest preceding text in the DOM (common pattern when
  // a form uses no real <label> element, just adjacent text).
  let sibling = field.previousElementSibling;
  while (sibling) {
    const text = sibling.innerText || sibling.textContent;
    if (text && text.trim()) return text.trim();
    sibling = sibling.previousElementSibling;
  }
  return "";
}

// Native setters bypass React/Vue's tracked value - plain field.value = x
// leaves the framework's internal state out of sync, so typed-looking input
// never "sticks" from the framework's point of view. This is the standard
// workaround: call the native setter, then dispatch the events the framework
// listens for, same as application_filler.py does server-side via Playwright.
function setReactValue(field, value) {
  const proto = field.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}

function fillField(field, value) {
  const tag = field.tagName;
  const type = (field.getAttribute("type") || "").toLowerCase();

  if (tag === "SELECT") {
    const target = normalize(value);
    for (const option of field.options) {
      if (normalize(option.text) === target || normalize(option.text).includes(target)) {
        field.value = option.value;
        field.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
    }
    return false;
  }

  if (type === "checkbox" || type === "radio") {
    const target = normalize(value);
    if (target === "yes" || target === "true" || target === normalize(field.value)) {
      field.click();
      return true;
    }
    return false;
  }

  if (tag === "INPUT" || tag === "TEXTAREA") {
    setReactValue(field, value);
    return true;
  }

  return false;
}

// Any answer value that looks like a document filename (the backend's own
// Playwright run only ever puts a filename here, for whatever field it found
// the resume upload slot under - could be labeled "Attach", "Resume/CV",
// "Upload Resume", anything) - used as a second, label-independent signal
// that a file input is the resume slot. A regex on the label text alone
// isn't enough: confirmed directly that this exact Stripe form labels its
// upload field just "Attach", which "resume|cv|cover letter" never matches.
const RESUME_FILENAME_RE = /\.(pdf|docx?|rtf|odt)$/i;

function autofillFrame(answers, skipFilePicker) {
  const normalizedAnswers = {};
  for (const [label, value] of Object.entries(answers)) {
    normalizedAnswers[normalize(label)] = value;
  }
  const hasResumeLikeAnswer = Object.values(answers).some((v) => typeof v === "string" && RESUME_FILENAME_RE.test(v));

  const fields = document.querySelectorAll("input:not([type=hidden]), textarea, select");
  let filledCount = 0;
  const filePickersOpened = [];

  for (const field of fields) {
    const label = getFieldLabel(field);

    // No script, anywhere, can set a file input's value - hard browser
    // security restriction. Best we can do is open the OS picker for the
    // user to browse to the file themselves. Checked here, BEFORE the
    // label-match gate below (not inside fillField, which the loop only
    // reaches once a label already matched an answers key) - a live page's
    // resume/CV field can be labeled all sorts of things ("Resume/CV",
    // "Upload Resume", "Attach", etc.), so this matches on EITHER a label
    // keyword OR (when any backend answer is itself a document filename -
    // meaning the backend found a resume upload slot somewhere) the mere
    // presence of a file input at all, since most ATS forms have exactly
    // one. Confirmed regression against the previously-committed version of
    // this file, which always matched file inputs by label alone.
    if ((field.getAttribute("type") || "").toLowerCase() === "file") {
      // skipFilePicker is true when popup.js already attached the resume via
      // Chrome's debugger protocol (DOM.setFileInputFiles) before sending
      // this message - clicking the field here would pop open a picker on
      // top of an already-filled input, undoing nothing but confusing to see.
      if (
        !skipFilePicker &&
        (/resume|cv|cover letter|attach|upload/i.test(label) || hasResumeLikeAnswer) &&
        filePickersOpened.length === 0
      ) {
        field.click();
        filePickersOpened.push(label || "file upload");
      }
      continue;
    }

    if (!label) continue;
    const normalizedLabel = normalize(label);

    let value = normalizedAnswers[normalizedLabel];
    if (value === undefined) {
      // Substring fallback - the page's label and the backend's captured
      // label can differ slightly (e.g. trailing "(optional)" text).
      const matchKey = Object.keys(normalizedAnswers).find(
        (k) => normalizedLabel.includes(k) || k.includes(normalizedLabel)
      );
      if (matchKey) value = normalizedAnswers[matchKey];
    }
    if (value === undefined) continue;

    const result = fillField(field, value);
    if (result) {
      filledCount++;
    }
  }

  return { filledCount, totalFields: fields.length, filePickersOpened };
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "autofill") {
    try {
      const result = autofillFrame(message.answers || {}, !!message.skipFilePicker);
      sendResponse({ ok: true, result, frameUrl: window.location.href });
    } catch (err) {
      sendResponse({ ok: false, error: String(err) });
    }
  }
  return true;
});
