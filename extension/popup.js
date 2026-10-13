const API_BASE = "http://localhost:8000";

const statusEl = document.getElementById("status");
const fillBtn = document.getElementById("fillBtn");

function setStatus(text) {
  statusEl.textContent = text;
}

fillBtn.addEventListener("click", async () => {
  fillBtn.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) throw new Error("No active tab found.");

    const match = tab.url.match(/jobagent_app_id=([0-9a-fA-F-]{36})/);
    let appId = match ? match[1] : null;
    if (!appId) {
      appId = prompt("Couldn't find the application ID in this page's URL. Paste it from the email:");
    }
    if (!appId) {
      setStatus("Cancelled - no application ID.");
      return;
    }

    setStatus("Fetching filled data...");
    // Runs from the extension's own popup context (chrome-extension:// origin),
    // not the page's - so it's not subject to the job site's CSP connect-src,
    // unlike the old bookmarklet's fetch() which ran inside the page itself.
    const res = await fetch(`${API_BASE}/applications/${appId}/filled-answers`);
    if (!res.ok) throw new Error(`Backend request failed (${res.status})`);
    const data = await res.json();
    const answers = data.filled_answers || {};
    if (!Object.keys(answers).length) {
      setStatus("No filled data found for this application yet.");
      return;
    }

    setStatus("Filling page (including any embedded iframes)...");
    // Send to every frame individually, not just the top one - the real form
    // is very often inside a cross-origin iframe (e.g. Greenhouse embeds),
    // and chrome.tabs.sendMessage without a frameId only reaches the main frame.
    const frames = await chrome.webNavigation.getAllFrames({ tabId: tab.id });
    let totalFilled = 0;
    let totalFields = 0;
    const perFrame = [];
    const filePickers = [];

    for (const frame of frames) {
      try {
        const response = await chrome.tabs.sendMessage(
          tab.id,
          { type: "autofill", answers },
          { frameId: frame.frameId }
        );
        if (response && response.ok) {
          totalFilled += response.result.filledCount;
          totalFields += response.result.totalFields;
          if (response.result.filledCount > 0) {
            perFrame.push(`${response.result.filledCount} field(s) in ${new URL(response.frameUrl).hostname}`);
          }
          if (response.result.filePickersOpened && response.result.filePickersOpened.length) {
            filePickers.push(...response.result.filePickersOpened);
          }
        }
      } catch (err) {
        // Frame had no content script response (e.g. about:blank, or still
        // loading) - skip it, not an error worth surfacing.
      }
    }

    let msg = `Filled ${totalFilled} field(s) across ${frames.length} frame(s).\n`;
    msg += perFrame.length ? perFrame.join("\n") : "Nothing matched on this page.";
    if (filePickers.length) {
      msg += `\n\nOpened the file picker for: ${filePickers.join(", ")} - browse to your resume manually (no script can do this step, browsers block it for security).`;
    }
    setStatus(msg);
  } catch (err) {
    setStatus("Error: " + err.message);
  } finally {
    fillBtn.disabled = false;
  }
});
