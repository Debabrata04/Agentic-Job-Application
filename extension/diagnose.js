/**
 * One-shot diagnostic: run this in the DevTools console, inside the SAME
 * frame the real form lives in (select it from the frame dropdown at the
 * top-left of the Console panel if the form is in an iframe). Copies a full
 * report of every fillable field on the page to your clipboard - run once,
 * paste the result back, and every field gets fixed in one pass instead of
 * one at a time.
 */
(async function () {
  function labelTextFor(el) {
    var text = el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("name") || "";
    if (!text) {
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
    return (text || "").trim();
  }

  var fields = document.querySelectorAll("input:not([type=hidden]), textarea, select");
  var report = [];

  for (var i = 0; i < fields.length; i++) {
    var el = fields[i];
    var tag = el.tagName.toLowerCase();
    var info = {
      index: i,
      tag: tag,
      type: el.type || null,
      id: el.id || null,
      role: el.getAttribute("role") || null,
      ariaHaspopup: el.getAttribute("aria-haspopup") || null,
      detectedLabel: labelTextFor(el),
      isNativeSelect: tag === "select",
    };

    if (tag === "select") {
      info.nativeOptions = Array.prototype.map.call(el.options, function (o) {
        return o.text.trim();
      });
    }

    // For anything that looks like a custom combobox (react-select etc.), try
    // opening it and see what option list (if any) actually renders.
    if (el.getAttribute("role") === "combobox" && el.getAttribute("aria-haspopup") === "true") {
      try {
        el.focus();
        el.click();
        await new Promise(function (r) {
          setTimeout(r, 600);
        });
        var options = document.querySelectorAll('[role="option"]');
        info.comboboxOptionsFound = options.length;
        info.comboboxOptionsText = Array.prototype.slice.call(options, 0, 15).map(function (o) {
          return o.textContent.trim();
        });
        document.body.click(); // try to close the menu before moving to the next field
        await new Promise(function (r) {
          setTimeout(r, 200);
        });
      } catch (e) {
        info.comboboxError = e.message;
      }
    }

    report.push(info);
  }

  var json = JSON.stringify(report, null, 2);
  try {
    await navigator.clipboard.writeText(json);
    console.log("Diagnostic copied to clipboard (" + report.length + " fields). Paste it back.");
  } catch (e) {
    console.log("Clipboard write failed, printing instead - copy this manually:");
    console.log(json);
  }
})();
