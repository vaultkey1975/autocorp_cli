(() => {
  "use strict";

  const state = { sessionId: null, session: null, pollTimer: null };

  const el = (id) => document.getElementById(id);
  const chatLog = el("chat-log");
  const sessionList = el("session-list");
  const currentSessionInfo = el("current-session-info");
  const workflowSummary = el("workflow-summary");
  const publishingIndicator = el("publishing-lock-indicator");
  const textInput = el("text-input");
  const fileInput = el("file-input");
  const dropZone = el("drop-zone");
  const cancelBtn = el("cancel-btn");
  const statusPanel = el("status-panel");
  const statusToggleBtn = el("status-toggle-btn");

  async function api(path, options) {
    const resp = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `request failed: ${resp.status}`);
    }
    return resp.json();
  }

  function fieldRow(label, value) {
    const div = document.createElement("div");
    div.className = "field";
    const b = document.createElement("b");
    b.textContent = label + ": ";
    div.appendChild(b);
    div.appendChild(document.createTextNode(value === null || value === undefined ? "—" : String(value)));
    return div;
  }

  function renderWorkflowSummary(summary) {
    workflowSummary.innerHTML = "";
    if (!summary) {
      workflowSummary.classList.add("hidden");
      return;
    }
    workflowSummary.classList.remove("hidden");
    const rows = [
      ["Project", summary.project],
      ["Show", summary.show],
      ["Target duration (s)", summary.target_duration_seconds],
      ["Research", summary.research_file],
      ["Research status", summary.research_status],
      ["Approved script", summary.approved_script],
      ["Script checksum", summary.script_checksum_status],
      ["Host", summary.host],
      ["Voice", summary.voice],
      ["Guests / callers", summary.guests_or_callers],
      ["Audio / video", summary.media_mode],
      ["Current step", summary.current_step],
      ["Output path", summary.output_path],
      ["Listening gate", summary.listening_gate_status],
      ["Publishing", summary.publishing_lock_status],
    ];
    for (const [label, value] of rows) workflowSummary.appendChild(fieldRow(label, value));
  }

  function renderMessage(msg) {
    const div = document.createElement("div");
    div.className = `msg ${msg.role} ${msg.kind}`;
    const text = document.createElement("div");
    text.textContent = msg.text;
    div.appendChild(text);

    if (msg.kind === "listening_gate") {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.src = `/api/sessions/${state.sessionId}/audio`;
      div.appendChild(audio);
    }

    if (Array.isArray(msg.options) && msg.options.length) {
      const box = document.createElement("div");
      box.className = "options";
      for (const opt of msg.options) {
        const btn = document.createElement("button");
        btn.textContent = opt.label;
        btn.addEventListener("click", () => sendAnswer({ value: opt.value, confirm_draft: opt.confirm_draft || false }));
        box.appendChild(btn);
      }
      div.appendChild(box);
    }

    if (msg.technical_detail) {
      const toggle = document.createElement("div");
      toggle.className = "tech-toggle";
      toggle.textContent = "Technical details";
      const detail = document.createElement("div");
      detail.className = "tech-detail hidden";
      detail.textContent = msg.technical_detail;
      toggle.addEventListener("click", () => detail.classList.toggle("hidden"));
      div.appendChild(toggle);
      div.appendChild(detail);
    }
    return div;
  }

  function renderSession(detail) {
    state.session = detail;
    chatLog.innerHTML = "";
    for (const msg of detail.messages) chatLog.appendChild(renderMessage(msg));
    chatLog.scrollTop = chatLog.scrollHeight;

    currentSessionInfo.textContent = `${detail.title} — ${detail.status}`;
    renderWorkflowSummary(detail.workflow_summary);

    const eligible = detail.workflow_summary && detail.workflow_summary.publishing_lock_status === "eligible";
    publishingIndicator.textContent = eligible ? "Publishing eligible (not published)" : "Publishing locked";
    publishingIndicator.classList.toggle("eligible", !!eligible);

    const pq = detail.pending_question;
    textInput.disabled = detail.status !== "awaiting_input" && detail.status !== "new" && detail.status !== "failed";
    if (pq && (pq.input_kind === "text" || pq.input_kind === "buttons_or_text" || pq.input_kind === "file_or_text")) {
      textInput.placeholder = pq.text;
    } else {
      textInput.placeholder = "Type a message...";
    }
    dropZone.classList.toggle("hidden", !(pq && pq.input_kind === "file_or_text"));
    cancelBtn.classList.toggle("hidden", detail.status !== "awaiting_input" && detail.status !== "running");

    if (detail.status === "failed" && detail.error && detail.error.retry_safe) {
      showResumeButton();
    }

    highlightActiveSession();
    schedulePoll(detail.status);
  }

  function showResumeButton() {
    if (chatLog.querySelector(".resume-btn")) return;
    const btn = document.createElement("button");
    btn.textContent = "Resume";
    btn.className = "primary-btn resume-btn";
    btn.style.alignSelf = "flex-start";
    btn.addEventListener("click", async () => {
      const detail = await api(`/api/sessions/${state.sessionId}/resume`, { method: "POST" });
      renderSession(detail);
    });
    chatLog.appendChild(btn);
  }

  function schedulePoll(status) {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    if (status !== "running") return;
    state.pollTimer = setTimeout(async () => {
      if (!state.sessionId) return;
      const detail = await api(`/api/sessions/${state.sessionId}`);
      renderSession(detail);
    }, 3000);
  }

  async function refreshSessionList() {
    const sessions = await api("/api/sessions");
    sessionList.innerHTML = "";
    for (const s of sessions) {
      const item = document.createElement("div");
      item.className = "session-item" + (s.session_id === state.sessionId ? " active" : "");
      item.dataset.sessionId = s.session_id;
      const title = document.createElement("span");
      title.textContent = s.title;
      const status = document.createElement("span");
      status.className = "session-status" + (s.status === "failed" ? " failed" : "");
      status.textContent = s.status;
      item.appendChild(title);
      item.appendChild(status);
      item.addEventListener("click", () => openSession(s.session_id));
      sessionList.appendChild(item);
    }
  }

  function highlightActiveSession() {
    for (const item of sessionList.children) {
      item.classList.toggle("active", item.dataset.sessionId === state.sessionId);
    }
  }

  async function openSession(sessionId) {
    state.sessionId = sessionId;
    const detail = await api(`/api/sessions/${sessionId}`);
    renderSession(detail);
  }

  async function startNewSession(message) {
    const detail = await api("/api/sessions", { method: "POST", body: JSON.stringify({ message }) });
    state.sessionId = detail.session_id;
    renderSession(detail);
    await refreshSessionList();
  }

  async function sendAnswer(payload) {
    if (!state.sessionId) return;
    const detail = await api(`/api/sessions/${state.sessionId}/answer`, { method: "POST", body: JSON.stringify(payload) });
    renderSession(detail);
    await refreshSessionList();
  }

  async function uploadFile(file) {
    if (!state.sessionId || !state.session || !state.session.pending_question) return;
    const kind = state.session.pending_question.field === "script" ? "script" : "research";
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch(`/api/sessions/${state.sessionId}/upload?kind=${kind}`, { method: "POST", body: form });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      alert(body.detail || "upload failed");
      return;
    }
    const record = await resp.json();
    await sendAnswer({ upload_id: record.upload_id });
  }

  el("new-session-btn").addEventListener("click", async () => {
    state.sessionId = null;
    chatLog.innerHTML = "";
    workflowSummary.classList.add("hidden");
    currentSessionInfo.textContent = "No active session";
    textInput.value = "";
    textInput.focus();
  });

  el("chat-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const text = textInput.value.trim();
    if (!text) return;
    textInput.value = "";
    if (!state.sessionId) {
      await startNewSession(text);
      return;
    }
    const pq = state.session && state.session.pending_question;
    if (pq && pq.field === "research") await sendAnswer({ text });
    else if (pq && pq.field === "script") await sendAnswer({ text });
    else await sendAnswer({ text });
  });

  el("file-btn").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) uploadFile(fileInput.files[0]);
    fileInput.value = "";
  });

  ["dragover"].forEach((evt) =>
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.add("drag-over");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.remove("drag-over");
    })
  );
  dropZone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });

  cancelBtn.addEventListener("click", async () => {
    if (!state.sessionId) return;
    const detail = await api(`/api/sessions/${state.sessionId}/cancel`, { method: "POST" });
    renderSession(detail);
  });

  statusToggleBtn.addEventListener("click", async () => {
    const willShow = statusPanel.classList.contains("hidden");
    if (willShow) {
      statusPanel.innerHTML = "Loading...";
      statusPanel.classList.remove("hidden");
      statusToggleBtn.textContent = "Hide status";
      const report = await api("/api/status");
      statusPanel.innerHTML = "";
      for (const check of report.checks) {
        const row = document.createElement("div");
        row.className = "status-check";
        const label = document.createElement("span");
        label.textContent = check.label;
        const value = document.createElement("span");
        const cls = check.status === "ok" ? "ok" : check.status === "Not checked" ? "not-checked" : "unavailable";
        value.className = cls;
        value.textContent = check.status === "ok" ? "OK" : check.status === "Not checked" ? "Not checked" : "Unavailable";
        row.appendChild(label);
        row.appendChild(value);
        row.title = check.detail || "";
        statusPanel.appendChild(row);
      }
    } else {
      statusPanel.classList.add("hidden");
      statusToggleBtn.textContent = "Show status";
    }
  });

  refreshSessionList();
})();
