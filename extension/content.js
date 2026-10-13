/**
 * Content script - runs once per frame (all_frames: true in manifest.json),
 * including cross-origin iframes like Greenhouse's embedded application form.
 * That's the whole reason this is an extension instead of a bookmarklet: a
 * bookmarklet runs in the TOP frame's JS context and can't reach into a
 * cross-origin iframe (blocked by the Same-Origin Policy) - a content script
 * is injected directly INTO each frame by the browser itself, so it has
 * native access to that frame's own DOM regardless of origin.
 *
 * Same label-matching heuristic as the old bookmarklet.js (fuzzy match on
 * aria-label/placeholder/name/label[for]) - kept here since bookmarklet.js
 * is no longer the active mechanism.
 */

function labelTextFor(el) {
  var text =
    el.getAttribute("aria-label") ||
    el.getAttribute("placeholder") ||
    el.getAttribute("name") ||
    "";
  if (!text) {
    // react-select (and many other component libraries) label their input via
    // aria-labelledby pointing at a separate element's id, rather than a
    // placeholder/aria-label/label[for] - e.g. <input aria-labelledby="country-label">.
    var labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      text = labelledBy
        .split(/\s+/)
        .map(function (id) {
          var ref = document.getElementById(id);
          return ref ? ref.textContent : "";
        })
        .join(" ");
    }
  }
  if (!text && el.id) {
    var labelEl = document.querySelector('label[for="' + el.id + '"]');
    if (labelEl) text = labelEl.textContent;
  }
  return (text || "").trim().toLowerCase();
}

function isReactSelectInput(el) {
  return (
    el.tagName.toLowerCase() === "input" &&
    el.getAttribute("role") === "combobox" &&
    el.getAttribute("aria-haspopup") === "true"
  );
}

/** react-select's input only filters a list that renders elsewhere in the DOM
 * when you type into it - there's no value to just "set". Real selection
 * needs: open it, type to filter, wait for the matching [role=option] to
 * render, then click it (same as a real user would, since nothing here is a
 * native form control react-select will accept a raw .value on). */
function fillReactSelectLike(el, value) {
  return new Promise(function (resolve) {
    el.focus();
    console.log("[JobAgent] fillReactSelectLike: focusing/clicking", el, "target value:", value);
    el.click();
    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));

    var attempts = 0;
    var poll = setInterval(function () {
      attempts++;
      var options = document.querySelectorAll('[role="option"]');
      console.log("[JobAgent] poll attempt", attempts, "- found", options.length, "[role=option] element(s):",
        Array.prototype.map.call(options, function (o) { return o.textContent.trim(); }));
      var target = String(value).trim().toLowerCase();
      var match =
        Array.prototype.find.call(options, function (o) {
          return o.textContent.trim().toLowerCase() === target;
        }) ||
        Array.prototype.find.call(options, function (o) {
          return o.textContent.trim().toLowerCase().indexOf(target) !== -1;
        });
      if (match) {
        console.log("[JobAgent] matched option, clicking:", match);
        clearInterval(poll);
        match.scrollIntoView({ block: "nearest" });
        match.click();
        resolve(true);
      } else if (attempts > 15) {
        console.log("[JobAgent] gave up after", attempts, "attempts - no matching option ever appeared for value:", value);
        clearInterval(poll);
        el.blur();
        resolve(false);
      }
    }, 200);
  });
}

function bestLabelMatch(fieldLabel, labels) {
  var best = null;
  var bestScore = 0;
  for (var i = 0; i < labels.length; i++) {
    var candidate = labels[i].toLowerCase();
    if (!fieldLabel || !candidate) continue;
    var score = 0;
    if (fieldLabel === candidate) score = 100;
    else if (fieldLabel.indexOf(candidate) !== -1 || candidate.indexOf(fieldLabel) !== -1) {
      score = Math.min(fieldLabel.length, candidate.length);
    }
    if (score > bestScore) {
      bestScore = score;
      best = labels[i];
    }
  }
  return bestScore > 0 ? best : null;
}

function fillNative(el, value) {
  var tag = el.tagName.toLowerCase();
  if (tag === "select") {
    var opt = Array.prototype.find.call(el.options, function (o) {
      return o.text.trim().toLowerCase() === String(value).trim().toLowerCase();
    });
    if (opt) el.value = opt.value;
    else return false;
  } else {
    var proto = tag === "textarea" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value); // bypasses some frameworks' input blocking
  }
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  return true;
}

async function autofillThisFrame(answers) {
  var labels = Object.keys(answers);
  var fields = document.querySelectorAll("input:not([type=hidden]), textarea, select");
  console.log("[JobAgent] autofillThisFrame start -", location.href, "- found", fields.length, "field(s), have", labels.length, "answer label(s):", labels);
  var filledCount = 0;
  var filledLabels = [];
  var filePickersOpened = [];

  for (var i = 0; i < fields.length; i++) {
    var el = fields[i];

    // No script anywhere - extension or not - can programmatically put a file
    // into an <input type=file>; browsers hard-block it for security (it's
    // why even Playwright needs a privileged CDP call, not DOM manipulation,
    // to do this). Best we can do: open the native file picker for the user,
    // so they only have to browse to the file instead of finding the button.
    if (el.tagName.toLowerCase() === "input" && el.type === "file") {
      var label = labelTextFor(el);
      if (/resume|cv|cover letter/.test(label) && filePickersOpened.length === 0) {
        try {
          el.click();
          filePickersOpened.push(label || "file upload");
        } catch (e) {
          // ignore - clicking can fail if this isn't within a direct user gesture
        }
      }
      continue;
    }

    var fieldLabel = labelTextFor(el);
    var match = bestLabelMatch(fieldLabel, labels);
    console.log("[JobAgent] field:", el, "detected label:", JSON.stringify(fieldLabel), "-> matched answer:", match);
    if (!match) continue;

    var reactSelect = isReactSelectInput(el);
    console.log("[JobAgent] routing via", reactSelect ? "fillReactSelectLike" : "fillNative", "for match:", match);
    var filled = reactSelect
      ? await fillReactSelectLike(el, answers[match])
      : fillNative(el, answers[match]);

    if (filled) {
      filledCount++;
      filledLabels.push(match);
    }
  }

  return {
    filledCount: filledCount,
    filledLabels: filledLabels,
    totalFields: fields.length,
    filePickersOpened: filePickersOpened,
  };
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "autofill") {
    autofillThisFrame(message.answers || {})
      .then(function (result) {
        sendResponse({ ok: true, frameUrl: location.href, result: result });
      })
      .catch(function (err) {
        sendResponse({ ok: false, frameUrl: location.href, error: err.message });
      });
    return true; // keep the message channel open for the async response above
  }
});
