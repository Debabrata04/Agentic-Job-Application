const DEFAULT_API_BASE = "http://15.206.230.236:8000";

const state = {
  apiBase: localStorage.getItem("apiBase") || DEFAULT_API_BASE,
  userId: localStorage.getItem("userId") || null,
  userEmail: localStorage.getItem("userEmail") || null,
};

const apiBaseInput = document.getElementById("apiBaseInput");
apiBaseInput.value = state.apiBase;
apiBaseInput.addEventListener("change", () => {
  state.apiBase = apiBaseInput.value.trim() || DEFAULT_API_BASE;
  localStorage.setItem("apiBase", state.apiBase);
});

function apiUrl(path) {
  return `${state.apiBase.replace(/\/$/, "")}${path}`;
}

async function apiRequest(path, { method = "GET", body, form, isFormData = false } = {}) {
  const opts = { method, headers: {} };
  if (isFormData) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(apiUrl(path), opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch (_) {}
    throw new Error(detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

function setMsg(el, text, type) {
  el.textContent = text || "";
  el.className = "msg" + (type ? ` ${type}` : "");
}

document.querySelectorAll(".tabs").forEach((tabs) => {
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    const tabName = btn.dataset.tab;
    const siblingButtons = tabs.querySelectorAll(".tab-btn");
    siblingButtons.forEach((b) => b.classList.toggle("active", b === btn));
    const scope = tabs.nextElementSibling ? tabs.parentElement : tabs.parentElement;
    scope.querySelectorAll(":scope > .tab-pane").forEach((p) => {
      const isTarget = p.id === tabName;
      p.classList.toggle("active", isTarget);
      p.classList.toggle("hidden", !isTarget);
    });
  });
});

const authSection = document.getElementById("authSection");
const dashboard = document.getElementById("dashboard");
const userBadge = document.getElementById("userBadge");
const authMsg = document.getElementById("authMsg");

function showDashboard() {
  authSection.classList.add("hidden");
  dashboard.classList.remove("hidden");
  userBadge.classList.remove("hidden");
  userBadge.textContent = state.userEmail || "Signed in";
  loadAll();
}

function showAuth() {
  dashboard.classList.add("hidden");
  authSection.classList.remove("hidden");
  userBadge.classList.add("hidden");
}

function setUser(user) {
  state.userId = user.id;
  state.userEmail = user.email;
  localStorage.setItem("userId", user.id);
  localStorage.setItem("userEmail", user.email);
}

document.getElementById("loginBtn").addEventListener("click", async () => {
  const email = document.getElementById("loginEmail").value.trim();
  if (!email) return setMsg(authMsg, "Enter your email", "error");
  try {
    setMsg(authMsg, "Looking up account...");
    const users = await apiRequest("/users");
    const match = users.find((u) => u.email.toLowerCase() === email.toLowerCase());
    if (!match) {
      setMsg(authMsg, "No account found with that email. Create one instead.", "error");
      return;
    }
    setUser(match);
    setMsg(authMsg, "");
    showDashboard();
  } catch (err) {
    setMsg(authMsg, err.message, "error");
  }
});

document.getElementById("createUserBtn").addEventListener("click", async () => {
  const full_name = document.getElementById("newFullName").value.trim();
  const email = document.getElementById("newEmail").value.trim();
  const phone = document.getElementById("newPhone").value.trim();
  const years = document.getElementById("newYears").value;
  if (!full_name || !email) return setMsg(authMsg, "Name and email are required", "error");
  try {
    setMsg(authMsg, "Creating account...");
    const user = await apiRequest("/users", {
      method: "POST",
      body: {
        full_name,
        email,
        phone: phone || null,
        years_of_experience: years ? parseFloat(years) : null,
      },
    });
    setUser(user);
    setMsg(authMsg, "");
    showDashboard();
  } catch (err) {
    setMsg(authMsg, err.message, "error");
  }
});

function userPath(path) {
  return `/users/${state.userId}${path}`;
}

// ---------- Profile ----------
const profileFields = [
  "address_line1", "address_line2", "city", "state", "postal_code", "country",
  "linkedin_url", "portfolio_url", "github_url", "work_authorization",
  "gender", "race_ethnicity", "veteran_status", "disability_status",
  "expected_salary_min", "expected_salary_max", "notice_period_days",
];
const profileCheckboxes = ["requires_sponsorship", "willing_to_relocate"];

async function loadProfile() {
  const msg = document.getElementById("profileMsg");
  try {
    const profile = await apiRequest(userPath("/profile"));
    profileFields.forEach((f) => {
      const el = document.getElementById(`p_${f}`);
      if (el) el.value = profile[f] ?? "";
    });
    profileCheckboxes.forEach((f) => {
      const el = document.getElementById(`p_${f}`);
      if (el) el.checked = !!profile[f];
    });
  } catch (err) {
    // 404 is fine for a not-yet-created profile
  }
}

document.getElementById("saveProfileBtn").addEventListener("click", async () => {
  const msg = document.getElementById("profileMsg");
  const payload = {};
  profileFields.forEach((f) => {
    const el = document.getElementById(`p_${f}`);
    const val = el.value.trim();
    if (f.startsWith("expected_salary") || f === "notice_period_days") {
      payload[f] = val ? parseInt(val, 10) : null;
    } else {
      payload[f] = val || null;
    }
  });
  profileCheckboxes.forEach((f) => {
    payload[f] = document.getElementById(`p_${f}`).checked;
  });
  try {
    setMsg(msg, "Saving...");
    await apiRequest(userPath("/profile"), { method: "PUT", body: payload });
    setMsg(msg, "Profile saved.", "success");
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

// ---------- Resumes ----------
async function loadResumes() {
  const list = document.getElementById("resumeList");
  list.innerHTML = "Loading...";
  try {
    const resumes = await apiRequest(userPath("/resumes"));
    if (!resumes.length) {
      list.innerHTML = '<p class="meta">No resumes uploaded yet.</p>';
      return;
    }
    list.innerHTML = "";
    resumes.forEach((r) => {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(r.label)} ${r.is_active ? '<span class="badge">Active</span>' : ""}</div>
          <div class="meta">${escapeHtml(r.original_filename)} · uploaded ${new Date(r.created_at).toLocaleDateString()}</div>
        </div>
        <div class="actions">
          <button class="secondary" data-action="download">Download</button>
          <button class="secondary" data-action="activate">${r.is_active ? "Deactivate" : "Activate"}</button>
          <button class="secondary" data-action="edit">Edit</button>
          <button class="danger-btn" data-action="delete">Delete</button>
        </div>`;
      item.querySelector('[data-action="download"]').addEventListener("click", () => {
        window.open(apiUrl(userPath(`/resumes/${r.id}/download`)), "_blank");
      });
      item.querySelector('[data-action="activate"]').addEventListener("click", async () => {
        await apiRequest(userPath(`/resumes/${r.id}/activate?is_active=${!r.is_active}`), { method: "PATCH" });
        loadResumes();
      });
      item.querySelector('[data-action="edit"]').addEventListener("click", () => {
        editingResumeId = r.id;
        document.getElementById("resumeLabel").value = r.label;
        document.getElementById("resumeFile").removeAttribute("required");
        document.getElementById("uploadResumeBtn").textContent = "Save changes";
        document.getElementById("cancelResumeEditBtn").classList.remove("hidden");
      });
      item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
        if (!confirm("Delete this resume?")) return;
        await apiRequest(userPath(`/resumes/${r.id}`), { method: "DELETE" });
        loadResumes();
      });
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = `<p class="meta">${escapeHtml(err.message)}</p>`;
  }
}

let editingResumeId = null;

function resetResumeForm() {
  editingResumeId = null;
  document.getElementById("resumeLabel").value = "";
  document.getElementById("resumeFile").value = "";
  document.getElementById("uploadResumeBtn").textContent = "Upload resume";
  document.getElementById("cancelResumeEditBtn").classList.add("hidden");
}

document.getElementById("cancelResumeEditBtn").addEventListener("click", resetResumeForm);

document.getElementById("uploadResumeBtn").addEventListener("click", async () => {
  const msg = document.getElementById("resumeMsg");
  const label = document.getElementById("resumeLabel").value.trim();
  const fileInput = document.getElementById("resumeFile");
  const file = fileInput.files[0];

  if (editingResumeId) {
    if (!label) return setMsg(msg, "Label is required", "error");
    const form = new FormData();
    form.append("label", label);
    try {
      setMsg(msg, "Saving...");
      await apiRequest(userPath(`/resumes/${editingResumeId}`), { method: "PATCH", form, isFormData: true });
      setMsg(msg, "Resume updated.", "success");
      resetResumeForm();
      loadResumes();
    } catch (err) {
      setMsg(msg, err.message, "error");
    }
    return;
  }

  if (!label || !file) return setMsg(msg, "Provide a label and a file", "error");
  const form = new FormData();
  form.append("label", label);
  form.append("file", file);
  try {
    setMsg(msg, "Uploading...");
    await apiRequest(userPath("/resumes"), { method: "POST", form, isFormData: true });
    setMsg(msg, "Resume uploaded.", "success");
    resetResumeForm();
    loadResumes();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

// ---------- Work Experience ----------
async function loadWork() {
  const list = document.getElementById("workList");
  list.innerHTML = "Loading...";
  try {
    const items = await apiRequest(userPath("/work-experiences"));
    if (!items.length) {
      list.innerHTML = '<p class="meta">No work experience added yet.</p>';
      return;
    }
    list.innerHTML = "";
    items.forEach((w) => {
      const item = document.createElement("div");
      item.className = "list-item";
      const dates = `${w.start_date || "?"} – ${w.is_current ? "Present" : w.end_date || "?"}`;
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(w.title)} at ${escapeHtml(w.company)}</div>
          <div class="meta">${escapeHtml(w.location || "")} · ${dates}</div>
        </div>
        <div class="actions">
          <button class="secondary" data-action="edit">Edit</button>
          <button class="danger-btn" data-action="delete">Delete</button>
        </div>`;
      item.querySelector('[data-action="edit"]').addEventListener("click", () => {
        editingWorkId = w.id;
        document.getElementById("w_company").value = w.company || "";
        document.getElementById("w_title").value = w.title || "";
        document.getElementById("w_location").value = w.location || "";
        document.getElementById("w_start_date").value = w.start_date || "";
        document.getElementById("w_end_date").value = w.end_date || "";
        document.getElementById("w_is_current").checked = !!w.is_current;
        document.getElementById("w_description").value = w.description || "";
        document.getElementById("addWorkBtn").textContent = "Save changes";
        document.getElementById("cancelWorkEditBtn").classList.remove("hidden");
      });
      item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
        if (!confirm("Delete this entry?")) return;
        await apiRequest(userPath(`/work-experiences/${w.id}`), { method: "DELETE" });
        loadWork();
      });
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = `<p class="meta">${escapeHtml(err.message)}</p>`;
  }
}

let editingWorkId = null;

function resetWorkForm() {
  editingWorkId = null;
  ["w_company", "w_title", "w_location", "w_start_date", "w_end_date", "w_description"].forEach(
    (id) => (document.getElementById(id).value = "")
  );
  document.getElementById("w_is_current").checked = false;
  document.getElementById("addWorkBtn").textContent = "Add work experience";
  document.getElementById("cancelWorkEditBtn").classList.add("hidden");
}

document.getElementById("cancelWorkEditBtn").addEventListener("click", resetWorkForm);

document.getElementById("addWorkBtn").addEventListener("click", async () => {
  const msg = document.getElementById("workMsg");
  const payload = {
    company: document.getElementById("w_company").value.trim(),
    title: document.getElementById("w_title").value.trim(),
    location: document.getElementById("w_location").value.trim() || null,
    start_date: document.getElementById("w_start_date").value || null,
    end_date: document.getElementById("w_end_date").value || null,
    is_current: document.getElementById("w_is_current").checked,
    description: document.getElementById("w_description").value.trim() || null,
  };
  if (!payload.company || !payload.title) return setMsg(msg, "Company and title are required", "error");
  try {
    if (editingWorkId) {
      setMsg(msg, "Saving...");
      await apiRequest(userPath(`/work-experiences/${editingWorkId}`), { method: "PATCH", body: payload });
      setMsg(msg, "Updated.", "success");
    } else {
      setMsg(msg, "Adding...");
      await apiRequest(userPath("/work-experiences"), { method: "POST", body: payload });
      setMsg(msg, "Added.", "success");
    }
    resetWorkForm();
    loadWork();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

// ---------- Education ----------
async function loadEducation() {
  const list = document.getElementById("eduList");
  list.innerHTML = "Loading...";
  try {
    const items = await apiRequest(userPath("/educations"));
    if (!items.length) {
      list.innerHTML = '<p class="meta">No education added yet.</p>';
      return;
    }
    list.innerHTML = "";
    items.forEach((e) => {
      const item = document.createElement("div");
      item.className = "list-item";
      const dates = `${e.start_date || "?"} – ${e.end_date || "?"}`;
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(e.degree || "")} ${escapeHtml(e.field_of_study ? "in " + e.field_of_study : "")}</div>
          <div class="meta">${escapeHtml(e.school)} · ${dates}${e.gpa ? ` · GPA ${e.gpa}` : ""}</div>
        </div>
        <div class="actions">
          <button class="secondary" data-action="edit">Edit</button>
          <button class="danger-btn" data-action="delete">Delete</button>
        </div>`;
      item.querySelector('[data-action="edit"]').addEventListener("click", () => {
        editingEduId = e.id;
        document.getElementById("e_school").value = e.school || "";
        document.getElementById("e_degree").value = e.degree || "";
        document.getElementById("e_field_of_study").value = e.field_of_study || "";
        document.getElementById("e_start_date").value = e.start_date || "";
        document.getElementById("e_end_date").value = e.end_date || "";
        document.getElementById("e_gpa").value = e.gpa ?? "";
        document.getElementById("addEduBtn").textContent = "Save changes";
        document.getElementById("cancelEduEditBtn").classList.remove("hidden");
      });
      item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
        if (!confirm("Delete this entry?")) return;
        await apiRequest(userPath(`/educations/${e.id}`), { method: "DELETE" });
        loadEducation();
      });
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = `<p class="meta">${escapeHtml(err.message)}</p>`;
  }
}

let editingEduId = null;

function resetEduForm() {
  editingEduId = null;
  ["e_school", "e_degree", "e_field_of_study", "e_start_date", "e_end_date", "e_gpa"].forEach(
    (id) => (document.getElementById(id).value = "")
  );
  document.getElementById("addEduBtn").textContent = "Add education";
  document.getElementById("cancelEduEditBtn").classList.add("hidden");
}

document.getElementById("cancelEduEditBtn").addEventListener("click", resetEduForm);

document.getElementById("addEduBtn").addEventListener("click", async () => {
  const msg = document.getElementById("eduMsg");
  const gpaVal = document.getElementById("e_gpa").value;
  const payload = {
    school: document.getElementById("e_school").value.trim(),
    degree: document.getElementById("e_degree").value.trim() || null,
    field_of_study: document.getElementById("e_field_of_study").value.trim() || null,
    start_date: document.getElementById("e_start_date").value || null,
    end_date: document.getElementById("e_end_date").value || null,
    gpa: gpaVal ? parseFloat(gpaVal) : null,
  };
  if (!payload.school) return setMsg(msg, "School is required", "error");
  try {
    if (editingEduId) {
      setMsg(msg, "Saving...");
      await apiRequest(userPath(`/educations/${editingEduId}`), { method: "PATCH", body: payload });
      setMsg(msg, "Updated.", "success");
    } else {
      setMsg(msg, "Adding...");
      await apiRequest(userPath("/educations"), { method: "POST", body: payload });
      setMsg(msg, "Added.", "success");
    }
    resetEduForm();
    loadEducation();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

// ---------- Documents ----------
async function loadDocuments() {
  const list = document.getElementById("docList");
  list.innerHTML = "Loading...";
  try {
    const docs = await apiRequest(userPath("/documents"));
    if (!docs.length) {
      list.innerHTML = '<p class="meta">No documents uploaded yet.</p>';
      return;
    }
    list.innerHTML = "";
    docs.forEach((d) => {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(d.doc_type)}</div>
          <div class="meta">${escapeHtml(d.original_filename)} · uploaded ${new Date(d.created_at).toLocaleDateString()}</div>
        </div>
        <div class="actions">
          <button class="secondary" data-action="download">Download</button>
          <button class="secondary" data-action="edit">Edit</button>
          <button class="danger-btn" data-action="delete">Delete</button>
        </div>`;
      item.querySelector('[data-action="download"]').addEventListener("click", () => {
        window.open(apiUrl(userPath(`/documents/${d.id}/download`)), "_blank");
      });
      item.querySelector('[data-action="edit"]').addEventListener("click", () => {
        editingDocId = d.id;
        document.getElementById("docType").value = d.doc_type;
        document.getElementById("docFile").removeAttribute("required");
        document.getElementById("uploadDocBtn").textContent = "Save changes";
        document.getElementById("cancelDocEditBtn").classList.remove("hidden");
      });
      item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
        if (!confirm("Delete this document?")) return;
        await apiRequest(userPath(`/documents/${d.id}`), { method: "DELETE" });
        loadDocuments();
      });
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = `<p class="meta">${escapeHtml(err.message)}</p>`;
  }
}

let editingDocId = null;

function resetDocForm() {
  editingDocId = null;
  document.getElementById("docType").value = "";
  document.getElementById("docFile").value = "";
  document.getElementById("uploadDocBtn").textContent = "Upload document";
  document.getElementById("cancelDocEditBtn").classList.add("hidden");
}

document.getElementById("cancelDocEditBtn").addEventListener("click", resetDocForm);

document.getElementById("uploadDocBtn").addEventListener("click", async () => {
  const msg = document.getElementById("docMsg");
  const doc_type = document.getElementById("docType").value.trim();
  const fileInput = document.getElementById("docFile");
  const file = fileInput.files[0];

  if (editingDocId) {
    if (!doc_type && !file) return setMsg(msg, "Change the type or choose a new file", "error");
    const form = new FormData();
    if (doc_type) form.append("doc_type", doc_type);
    if (file) form.append("file", file);
    try {
      setMsg(msg, "Saving...");
      await apiRequest(userPath(`/documents/${editingDocId}`), { method: "PATCH", form, isFormData: true });
      setMsg(msg, "Document updated.", "success");
      resetDocForm();
      loadDocuments();
    } catch (err) {
      setMsg(msg, err.message, "error");
    }
    return;
  }

  if (!doc_type || !file) return setMsg(msg, "Provide a type and a file", "error");
  const form = new FormData();
  form.append("doc_type", doc_type);
  form.append("file", file);
  try {
    setMsg(msg, "Uploading...");
    await apiRequest(userPath("/documents"), { method: "POST", form, isFormData: true });
    setMsg(msg, "Document uploaded.", "success");
    resetDocForm();
    loadDocuments();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

// ---------- Target Roles ----------
async function loadRoles() {
  const list = document.getElementById("roleList");
  list.innerHTML = "Loading...";
  try {
    const roles = await apiRequest(userPath("/target-roles"));
    if (!roles.length) {
      list.innerHTML = '<p class="meta">No target roles added yet.</p>';
      return;
    }
    list.innerHTML = "";
    roles.forEach((r) => {
      const item = document.createElement("div");
      item.className = "list-item";
      const years = r.min_years_experience;
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(r.title)} ${r.is_active ? '<span class="badge">Active</span>' : ""}</div>
          <div class="meta">${years !== null && years !== undefined ? `${years} yrs · ` : ""}${(r.keywords || []).join(", ")}</div>
        </div>
        <div class="actions">
          <button class="secondary" data-action="edit">Edit</button>
          <button class="danger-btn" data-action="delete">Delete</button>
        </div>`;
      item.querySelector('[data-action="edit"]').addEventListener("click", () => {
        editingRoleId = r.id;
        document.getElementById("r_title").value = r.title || "";
        document.getElementById("r_years").value = r.min_years_experience ?? "";
        document.getElementById("r_keywords").value = (r.keywords || []).join(", ");
        document.getElementById("addRoleBtn").textContent = "Save changes";
        document.getElementById("cancelRoleEditBtn").classList.remove("hidden");
      });
      item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
        if (!confirm("Delete this role?")) return;
        await apiRequest(userPath(`/target-roles/${r.id}`), { method: "DELETE" });
        loadRoles();
      });
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = `<p class="meta">${escapeHtml(err.message)}</p>`;
  }
}

let editingRoleId = null;

function resetRoleForm() {
  editingRoleId = null;
  ["r_title", "r_years", "r_keywords"].forEach((id) => (document.getElementById(id).value = ""));
  document.getElementById("addRoleBtn").textContent = "Add target role";
  document.getElementById("cancelRoleEditBtn").classList.add("hidden");
}

document.getElementById("cancelRoleEditBtn").addEventListener("click", resetRoleForm);

document.getElementById("addRoleBtn").addEventListener("click", async () => {
  const msg = document.getElementById("roleMsg");
  const title = document.getElementById("r_title").value.trim();
  const years = document.getElementById("r_years").value;
  const keywords = document.getElementById("r_keywords").value
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
  if (!title) return setMsg(msg, "Title is required", "error");
  const payload = {
    title,
    min_years_experience: years ? parseFloat(years) : null,
    max_years_experience: null,
    keywords: keywords.length ? keywords : null,
    is_active: true,
  };
  try {
    if (editingRoleId) {
      setMsg(msg, "Saving...");
      await apiRequest(userPath(`/target-roles/${editingRoleId}`), { method: "PATCH", body: payload });
      setMsg(msg, "Updated.", "success");
    } else {
      setMsg(msg, "Adding...");
      await apiRequest(userPath("/target-roles"), { method: "POST", body: payload });
      setMsg(msg, "Added.", "success");
    }
    resetRoleForm();
    loadRoles();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

// ---------- Applications ----------
const STATUS_LABELS = {
  pending_review: "Pending review",
  auto_applied: "Matched (awaiting pre-fill)",
  applied: "Submitted",
  needs_more_info: "Needs more info",
  rejected_by_user: "Rejected",
  failed: "Pre-fill failed",
};

async function loadApplications() {
  const list = document.getElementById("appsList");
  list.innerHTML = "Loading...";
  try {
    const apps = await apiRequest(userPath("/applications"));
    if (!apps.length) {
      list.innerHTML = '<p class="meta">No applications matched yet. These appear automatically once the 6-hour job cycle finds a match.</p>';
      return;
    }
    list.innerHTML = "";
    for (const a of apps) {
      let jobLabel = "Job posting";
      let applyUrl = null;
      let externalId = null;
      try {
        const job = await apiRequest(`/job-postings/${a.job_posting_id}`);
        jobLabel = `${job.title} at ${job.company}`;
        applyUrl = job.url;
        externalId = job.external_id;
      } catch (_) {}

      const item = document.createElement("div");
      item.className = "list-item";
      const missing = a.missing_fields || [];
      const optionalMissing = a.optional_missing_fields || [];
      const matchedSkills = a.matched_skills || [];
      const missingSkills = a.missing_skills || [];
      const filledCount = a.filled_answers ? Object.keys(a.filled_answers).length : 0;
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(jobLabel)} <span class="badge">${escapeHtml(STATUS_LABELS[a.status] || a.status)}</span></div>
          <div class="meta">Job ID: ${escapeHtml(a.job_posting_id)}${externalId ? ` (external: ${escapeHtml(externalId)})` : ""} · Application ID: ${escapeHtml(a.id)}</div>
          <div class="meta">
            ${a.similarity_score !== null && a.similarity_score !== undefined ? `Match score ${(a.similarity_score * 100).toFixed(0)}% · ` : ""}
            ${filledCount} field(s) pre-filled
            ${missing.length ? ` · Missing (required): ${missing.map(escapeHtml).join(", ")}` : ""}
            ${optionalMissing.length ? ` · Missing (optional): ${optionalMissing.map(escapeHtml).join(", ")}` : ""}
          </div>
          ${matchedSkills.length ? `<div class="meta">Skills matched: ${matchedSkills.map(escapeHtml).join(", ")}</div>` : ""}
          ${missingSkills.length ? `<div class="meta">Skills the JD wants that your profile doesn't show: ${missingSkills.map(escapeHtml).join(", ")}</div>` : ""}
          ${a.review_reason ? `<div class="meta">${escapeHtml(a.review_reason)}</div>` : ""}
          ${applyUrl ? `<div class="meta"><a href="${escapeHtml(applyUrl)}" target="_blank" rel="noopener">Open application form &rarr;</a></div>` : ""}
        </div>
        <div class="actions">
          ${applyUrl ? '<button class="secondary" data-action="open-live">Open and fill live</button>' : ""}
          ${filledCount ? '<button class="secondary" data-action="copy">Copy autofill data</button>' : ""}
          ${a.screenshot_path ? '<button class="secondary" data-action="screenshot">View screenshot</button>' : ""}
          ${a.status !== "applied" ? '<button data-action="submit">Mark as submitted</button>' : ""}
        </div>`;

      const openLiveBtn = item.querySelector('[data-action="open-live"]');
      if (openLiveBtn) {
        openLiveBtn.addEventListener("click", async () => {
          openLiveBtn.disabled = true;
          openLiveBtn.textContent = "Opening...";
          try {
            await apiRequest(userPath(`/applications/${a.id}/open-live`), { method: "POST" });
            openLiveBtn.textContent = "Browser window opening - check your desktop";
          } catch (err) {
            alert("Couldn't open live browser: " + err.message);
            openLiveBtn.textContent = "Open and fill live";
            openLiveBtn.disabled = false;
          }
        });
      }
      const copyBtn = item.querySelector('[data-action="copy"]');
      if (copyBtn) {
        copyBtn.addEventListener("click", async () => {
          const payload = JSON.stringify({
            filled_answers: a.filled_answers || {},
            missing_fields: a.missing_fields || [],
            optional_missing_fields: a.optional_missing_fields || [],
          });
          try {
            await navigator.clipboard.writeText(payload);
            copyBtn.textContent = "Copied!";
            setTimeout(() => (copyBtn.textContent = "Copy autofill data"), 1500);
          } catch (err) {
            alert("Couldn't copy to clipboard: " + err.message);
          }
        });
      }
      const screenshotBtn = item.querySelector('[data-action="screenshot"]');
      if (screenshotBtn) {
        screenshotBtn.addEventListener("click", () => {
          window.open(apiUrl(userPath(`/applications/${a.id}/screenshot`)), "_blank");
        });
      }
      const submitBtn = item.querySelector('[data-action="submit"]');
      if (submitBtn) {
        submitBtn.addEventListener("click", async () => {
          if (!confirm("Have you submitted this application on the job site?")) return;
          await apiRequest(userPath(`/applications/${a.id}/mark-submitted`), { method: "PATCH", body: {} });
          loadApplications();
        });
      }
      list.appendChild(item);
    }
  } catch (err) {
    list.innerHTML = `<p class="meta">${escapeHtml(err.message)}</p>`;
  }
}

document.getElementById("refreshAppsBtn").addEventListener("click", loadApplications);

document.getElementById("retryFailedBtn").addEventListener("click", async () => {
  const msg = document.getElementById("appsMsg");
  try {
    const result = await apiRequest(userPath("/applications/retry-failed"), { method: "POST" });
    setMsg(
      msg,
      `Retrying ${result.queued} failed application(s) in the background — this can take a while (one real browser run per job). Click Refresh periodically to see progress.`,
      "success"
    );
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

// ---------- Job Postings ----------
const postingsState = { offset: 0, limit: 25 };

async function loadJobPostings() {
  const list = document.getElementById("postingsList");
  const msg = document.getElementById("postingsMsg");
  const company = document.getElementById("postingCompanyFilter").value.trim();
  const title = document.getElementById("postingTitleFilter").value.trim();
  const params = new URLSearchParams();
  if (company) params.set("company", company);
  if (title) params.set("title", title);
  params.set("limit", postingsState.limit);
  params.set("offset", postingsState.offset);

  list.innerHTML = "Loading...";
  try {
    const countParams = new URLSearchParams(params);
    countParams.delete("limit");
    countParams.delete("offset");
    const [postings, countResult] = await Promise.all([
      apiRequest(`/job-postings?${params.toString()}`),
      apiRequest(`/job-postings/count?${countParams.toString()}`),
    ]);
    const total = countResult.count;
    const from = total === 0 ? 0 : postingsState.offset + 1;
    const to = Math.min(postingsState.offset + postingsState.limit, total);
    document.getElementById("postingsPageInfo").textContent = `${from}-${to} of ${total}`;
    document.getElementById("postingsPrevBtn").disabled = postingsState.offset === 0;
    document.getElementById("postingsNextBtn").disabled = to >= total;

    if (!postings.length) {
      list.innerHTML = '<p class="meta">No job postings found.</p>';
      setMsg(msg, "");
      return;
    }
    list.innerHTML = "";
    postings.forEach((p) => {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(p.title)}</div>
          <div class="meta">${escapeHtml(p.company)} · ${escapeHtml(p.location || "")} · ${escapeHtml(p.source)} · discovered ${new Date(p.discovered_at).toLocaleDateString()}</div>
        </div>
        <div class="actions">
          ${p.url ? `<a href="${escapeHtml(p.url)}" target="_blank" rel="noopener"><button class="secondary" type="button">Open posting</button></a>` : ""}
        </div>`;
      list.appendChild(item);
    });
    setMsg(msg, "");
  } catch (err) {
    list.innerHTML = "";
    setMsg(msg, err.message, "error");
  }
}

document.getElementById("postingSearchBtn").addEventListener("click", () => {
  postingsState.offset = 0;
  loadJobPostings();
});
document.getElementById("postingsPrevBtn").addEventListener("click", () => {
  postingsState.offset = Math.max(0, postingsState.offset - postingsState.limit);
  loadJobPostings();
});
document.getElementById("postingsNextBtn").addEventListener("click", () => {
  postingsState.offset += postingsState.limit;
  loadJobPostings();
});

// ---------- Job Sources ----------
let jobSourceCatalog = [];

async function loadJobSourceCatalog() {
  const select = document.getElementById("src_company");
  try {
    jobSourceCatalog = await apiRequest("/job-sources/catalog");
    if (!jobSourceCatalog.length) {
      select.innerHTML = '<option value="">No companies left to add</option>';
      return;
    }
    select.innerHTML = jobSourceCatalog
      .map((c, i) => `<option value="${i}">${escapeHtml(c.company_name)}</option>`)
      .join("");
  } catch (err) {
    select.innerHTML = '<option value="">Failed to load companies</option>';
  }
}

async function loadJobSources() {
  const list = document.getElementById("sourcesList");
  list.innerHTML = "Loading...";
  try {
    const sources = await apiRequest("/job-sources");
    if (!sources.length) {
      list.innerHTML = '<p class="meta">No companies configured yet. Add one above.</p>';
      return;
    }
    list.innerHTML = "";
    sources.forEach((s) => {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML = `
        <div>
          <div class="title">${escapeHtml(s.company_name)} ${s.is_active ? '<span class="badge">Active</span>' : ""}</div>
          <div class="meta">${escapeHtml(s.platform)} · slug: ${escapeHtml(s.slug)}</div>
          <div class="meta">Last poll: ${s.last_polled_at ? new Date(s.last_polled_at).toLocaleString() : "never"} ${s.last_poll_status ? `(${escapeHtml(s.last_poll_status)})` : ""}</div>
          ${s.last_poll_error ? `<div class="meta">${escapeHtml(s.last_poll_error)}</div>` : ""}
        </div>
        <div class="actions">
          <button class="secondary" data-action="discover">Poll now</button>
          <button class="secondary" data-action="toggle">${s.is_active ? "Deactivate" : "Activate"}</button>
          <button class="danger-btn" data-action="delete">Delete</button>
        </div>`;
      item.querySelector('[data-action="discover"]').addEventListener("click", async () => {
        await apiRequest(`/job-sources/${s.id}/discover`, { method: "POST" });
        loadJobSources();
      });
      item.querySelector('[data-action="toggle"]').addEventListener("click", async () => {
        await apiRequest(`/job-sources/${s.id}`, { method: "PATCH", body: { is_active: !s.is_active } });
        loadJobSources();
      });
      item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
        if (!confirm(`Stop tracking ${s.company_name}?`)) return;
        await apiRequest(`/job-sources/${s.id}`, { method: "DELETE" });
        loadJobSources();
      });
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = `<p class="meta">${escapeHtml(err.message)}</p>`;
  }
}

document.getElementById("addSourceBtn").addEventListener("click", async () => {
  const msg = document.getElementById("sourcesMsg");
  const select = document.getElementById("src_company");
  const entry = jobSourceCatalog[select.value];
  if (!entry) return setMsg(msg, "Pick a company from the list", "error");
  try {
    setMsg(msg, "Adding...");
    await apiRequest("/job-sources", {
      method: "POST",
      body: { company_name: entry.company_name, platform: entry.platform, slug: entry.slug, is_active: true },
    });
    setMsg(msg, "Added.", "success");
    await loadJobSourceCatalog();
    loadJobSources();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

document.getElementById("discoverAllBtn").addEventListener("click", async () => {
  const msg = document.getElementById("sourcesMsg");
  try {
    setMsg(msg, "Polling all companies - this can take a while...");
    await apiRequest("/job-sources/discover-all", { method: "POST" });
    setMsg(msg, "Done.", "success");
    loadJobSources();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
});

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function loadAll() {
  loadProfile();
  loadResumes();
  loadWork();
  loadEducation();
  loadDocuments();
  loadRoles();
  loadApplications();
  loadJobPostings();
  loadJobSourceCatalog();
  loadJobSources();
}

if (state.userId) {
  showDashboard();
} else {
  showAuth();
}
