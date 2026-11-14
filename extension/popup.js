const API_BASE = "http://localhost:8000";

const statusEl = document.getElementById("status");
const fillBtn = document.getElementById("fillBtn");

function setStatus(text) {
  statusEl.textContent = text;
}

function sendDebuggerCommand(tabId, sessionId, method, params) {
  return new Promise((resolve, reject) => {
    const target = sessionId ? { tabId, sessionId } : { tabId };
    chrome.debugger.sendCommand(target, method, params || {}, (result) => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve(result);
    });
  });
}

// Downloads the resume to the user's own Downloads folder (CDP's
// DOM.setFileInputFiles needs a real local path, not a byte stream - this
// is the one unavoidable side effect of true automatic attachment) and
// waits for the download to actually finish before returning its path.
async function downloadResumeLocally(resumeUrl, resumeFilename) {
  const fullUrl = `${API_BASE}${resumeUrl}`;
  const suggestedName = `job-agent-resume/${resumeFilename || "resume.pdf"}`;

  const downloadId = await new Promise((resolve, reject) => {
    chrome.downloads.download(
      { url: fullUrl, filename: suggestedName, conflictAction: "overwrite", saveAs: false },
      (id) => {
        if (chrome.runtime.lastError || id === undefined) {
          reject(new Error(chrome.runtime.lastError ? chrome.runtime.lastError.message : "Download failed to start"));
        } else {
          resolve(id);
        }
      }
    );
  });

  return new Promise((resolve, reject) => {
    const checkDone = () => {
      chrome.downloads.search({ id: downloadId }, (items) => {
        const item = items && items[0];
        if (item && item.state === "complete" && item.filename) {
          chrome.downloads.onChanged.removeListener(listener);
          resolve(item.filename);
        } else if (item && item.state === "interrupted") {
          chrome.downloads.onChanged.removeListener(listener);
          reject(new Error("Resume download was interrupted"));
        }
      });
    };
    const listener = (delta) => {
      if (delta.id === downloadId) checkDone();
    };
    chrome.downloads.onChanged.addListener(listener);
    checkDone(); // in case it's already complete by the time we get here
  });
}

// Finds the file input in ONE document/frame session and returns its CDP
// nodeId, or null if this particular frame has none. DOM.querySelector only
// searches within a single document - it does not reach into iframes, which
// is why the caller has to try this once per frame session.
async function findFileInputNodeId(tabId, sessionId) {
  await sendDebuggerCommand(tabId, sessionId, "DOM.enable", {});
  const { root } = await sendDebuggerCommand(tabId, sessionId, "DOM.getDocument", { depth: -1, pierce: true });
  try {
    const { nodeId } = await sendDebuggerCommand(tabId, sessionId, "DOM.querySelector", {
      nodeId: root.nodeId,
      selector: "input[type=file]",
    });
    return nodeId || null;
  } catch (err) {
    return null;
  }
}

// True, no-prompt automatic resume attachment - not plain DOM scripting
// (browsers hard-block a script from ever setting a file input's value,
// whatever runs it), but Chrome's own debugger protocol, the same
// privileged mechanism Playwright itself uses server-side for this exact
// step. Shows Chrome's own debugging banner on the tab while attached -
// that's a real, unavoidable UI side effect of this approach, not a bug.
// Returns true if a file input was found and set in some frame, false if
// none was found anywhere (caller falls back to the manual picker).
async function attachResumeViaDebugger(tabId, localPath) {
  await new Promise((resolve, reject) => {
    chrome.debugger.attach({ tabId }, "1.3", () => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve();
    });
  });

  const childSessionIds = [];
  const onEvent = (source, method, params) => {
    if (source.tabId === tabId && method === "Target.attachedToTarget" && params.sessionId) {
      childSessionIds.push(params.sessionId);
    }
  };
  chrome.debugger.onEvent.addListener(onEvent);

  try {
    // Cross-origin iframes (e.g. an embedded Greenhouse form) run as
    // separate CDP targets (OOPIFs) - this discovers each one as its own
    // debugger session so the file-input search below can check every
    // frame, not just the main one.
    await sendDebuggerCommand(tabId, undefined, "Target.setAutoAttach", {
      autoAttach: true,
      waitForDebuggerOnStart: false,
      flatten: true,
    });
    await new Promise((r) => setTimeout(r, 400)); // let attach events arrive

    const sessionsToTry = [undefined, ...childSessionIds]; // undefined = main frame
    for (const sessionId of sessionsToTry) {
      const nodeId = await findFileInputNodeId(tabId, sessionId);
      if (nodeId) {
        await sendDebuggerCommand(tabId, sessionId, "DOM.setFileInputFiles", {
          files: [localPath],
          nodeId,
        });
        return true;
      }
    }
    return false;
  } finally {
    chrome.debugger.onEvent.removeListener(onEvent);
    chrome.debugger.detach({ tabId }, () => {});
  }
}

fillBtn.addEventListener("click", async () => {
  fillBtn.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) throw new Error("No active tab found.");

    // Runs from the extension's own popup context (chrome-extension:// origin),
    // not the page's - so it's not subject to the job site's CSP connect-src,
    // unlike a bookmarklet's fetch() which would run inside the page itself.
    //
    // The #jobagent_app_id fragment in the email link is the fast path - read
    // it straight from the URL, no network round trip needed. But requiring
    // that fragment (or worse, prompting the user to type/paste an
    // application ID) breaks the moment the page was opened any other way -
    // a bookmark, a dashboard click, a retyped URL. So when the fragment
    // isn't there, fall back to asking the backend to find the application
    // from the page's own URL instead of asking the person.
    const match = tab.url.match(/jobagent_app_id=([0-9a-fA-F-]{36})/);
    let data;
    if (match) {
      setStatus("Fetching filled data...");
      const res = await fetch(`${API_BASE}/applications/${match[1]}/filled-answers`);
      if (!res.ok) throw new Error(`Backend request failed (${res.status})`);
      data = await res.json();
    } else {
      setStatus("Looking up this application by page URL...");
      const pageUrl = tab.url.split("#")[0];
      const res = await fetch(`${API_BASE}/applications/by-job-url?url=${encodeURIComponent(pageUrl)}`);
      if (!res.ok) {
        throw new Error(
          res.status === 404
            ? "Could not find a stored application for this page - it may not have been pre-filled yet."
            : `Backend request failed (${res.status})`
        );
      }
      data = await res.json();
    }
    const answers = data.filled_answers || {};
    if (!Object.keys(answers).length) {
      setStatus("No filled data found for this application yet.");
      return;
    }

    let resumeAttached = false;
    let resumeAttachError = null;
    if (data.resume_url) {
      setStatus("Downloading resume and attaching it automatically...");
      try {
        const localPath = await downloadResumeLocally(data.resume_url, data.resume_filename);
        resumeAttached = await attachResumeViaDebugger(tab.id, localPath);
      } catch (err) {
        resumeAttachError = err.message;
      }
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
          { type: "autofill", answers, skipFilePicker: resumeAttached },
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
    if (resumeAttached) {
      msg += "\n\nResume attached automatically.";
    } else if (resumeAttachError) {
      msg += `\n\nCouldn't auto-attach the resume (${resumeAttachError}) - falling back to manual.`;
    }
    if (filePickers.length) {
      msg += `\n\nOpened the file picker for: ${filePickers.join(", ")} - browse to your resume manually.`;
    }
    setStatus(msg);
  } catch (err) {
    setStatus("Error: " + err.message);
  } finally {
    fillBtn.disabled = false;
  }
});
