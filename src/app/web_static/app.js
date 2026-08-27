const state = {
  sessions: [],
  sessionId: "",
  activeRunId: "",
  events: new Set(),
  eventSource: null,
};

const els = {
  sessionList: document.querySelector("#sessionList"),
  newSession: document.querySelector("#newSession"),
  refreshSessions: document.querySelector("#refreshSessions"),
  sessionTitle: document.querySelector("#sessionTitle"),
  runState: document.querySelector("#runState"),
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  stopRun: document.querySelector("#stopRun"),
  currentRun: document.querySelector("#currentRun"),
  eventList: document.querySelector("#eventList"),
  approvalPanel: document.querySelector("#approvalPanel"),
  approvalQuestion: document.querySelector("#approvalQuestion"),
  approvalChoices: document.querySelector("#approvalChoices"),
  approvalInput: document.querySelector("#approvalInput"),
  sendApproval: document.querySelector("#sendApproval"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function setRunStatus(status) {
  els.runState.textContent = status || "idle";
  els.runState.dataset.status = status || "idle";
}

async function loadSessions(selectLatest = false) {
  state.sessions = await api("/api/sessions");
  renderSessions();
  if (!state.sessionId && state.sessions.length && selectLatest) {
    await selectSession(state.sessions[0].id);
  }
}

function renderSessions() {
  els.sessionList.innerHTML = "";
  if (!state.sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "还没有会话";
    els.sessionList.append(empty);
    return;
  }
  for (const session of state.sessions) {
    const button = document.createElement("button");
    button.className = "session-item";
    if (session.id === state.sessionId) button.classList.add("active");
    button.innerHTML = `
      <span class="session-id">${escapeHtml(session.id)}</span>
      <span class="session-meta">${escapeHtml(session.runtime_mode || "default")} · ${escapeHtml(formatTime(session.updated_at))}</span>
      ${session.active_status ? `<span class="session-status">${escapeHtml(session.active_status)}</span>` : ""}
    `;
    button.addEventListener("click", () => selectSession(session.id));
    els.sessionList.append(button);
  }
}

async function selectSession(sessionId) {
  const session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  state.sessionId = session.id;
  state.activeRunId = session.active_run_id || session.latest_run_id || "";
  els.sessionTitle.textContent = session.id;
  renderSessions();
  renderHistory(session.history || []);
  setRunStatus(session.active_status || "idle");
  clearApproval();
  if (state.activeRunId) {
    connectEvents(state.activeRunId);
  }
}

function renderHistory(history) {
  els.messages.innerHTML = "";
  if (!history.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = "<strong>准备开始</strong><span>发送一个任务，右侧会同步记录模型动作、工具调用和 checkpoint。</span>";
    els.messages.append(empty);
    return;
  }
  for (const item of history) {
    const role = item.role || "unknown";
    if (role === "tool") continue;
    addMessage(role, item.content || "");
  }
}

function addMessage(role, content) {
  const node = document.createElement("article");
  node.className = `message ${role}`;
  node.innerHTML = `<span>${escapeHtml(role)}</span><p>${escapeHtml(content)}</p>`;
  els.messages.append(node);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function connectEvents(runId) {
  if (!runId) return;
  if (state.eventSource) state.eventSource.close();
  state.events.clear();
  els.eventList.innerHTML = "";
  state.activeRunId = runId;
  els.currentRun.textContent = runId;
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  state.eventSource = source;
  const names = [
    "web_run_started",
    "jcode_run_bound",
    "run_started",
    "context_built",
    "model_requested",
    "model_parsed",
    "tool_requested",
    "tool_executed",
    "subagent_completed",
    "checkpoint_created",
    "final_readiness_decision",
    "memory_maintained",
    "run_finished",
    "approval_required",
    "approval_answered",
    "web_run_completed",
    "run_abort_requested",
    "run_aborted",
    "run_failed",
    "stream_closed",
  ];
  for (const name of names) {
    source.addEventListener(name, (event) => handleRunEvent(name, event));
  }
  source.onerror = () => {
    setRunStatus("disconnected");
  };
}

function handleRunEvent(name, event) {
  const payload = JSON.parse(event.data);
  const eventId = payload.event_id || event.lastEventId || `${name}:${Date.now()}`;
  if (state.events.has(eventId)) return;
  state.events.add(eventId);
  appendEvent(name, payload);
  if (payload.run_id) {
    state.activeRunId = payload.web_run_id || payload.run_id;
    els.currentRun.textContent = payload.run_id;
  }
  if (name === "approval_required") showApproval(payload);
  if (name === "approval_answered") clearApproval();
  if (name === "web_run_completed" || name === "run_finished") {
    setRunStatus("completed");
    if (payload.final_text) addMessage("assistant", payload.final_text);
    loadSessions(false).catch(console.error);
  }
  if (name === "run_failed") setRunStatus("failed");
  if (name === "run_abort_requested") setRunStatus("aborting");
  if (name === "run_aborted") setRunStatus("aborted");
  if (name === "approval_required") setRunStatus("waiting_approval");
  if (name === "run_started" || name === "tool_requested" || name === "model_requested") setRunStatus("running");
  if (name === "stream_closed" && state.eventSource) state.eventSource.close();
}

function appendEvent(name, payload) {
  const item = document.createElement("article");
  item.className = `event event-${name}`;
  const title = payload.name || payload.tool_name || name;
  const detail = eventDetail(payload);
  item.innerHTML = `
    <div class="event-head">
      <span class="event-name">${escapeHtml(title)}</span>
      <span class="event-time">${escapeHtml(formatTime(payload.created_at))}</span>
    </div>
    <div class="event-type">${escapeHtml(name)}</div>
    ${detail ? `<pre>${escapeHtml(detail)}</pre>` : ""}
  `;
  els.eventList.append(item);
  els.eventList.scrollTop = els.eventList.scrollHeight;
}

function eventDetail(payload) {
  const detail = {};
  for (const key of ["status", "error_type", "changed_files", "args", "metadata", "result", "action", "stop_reason", "question", "choices"]) {
    if (payload[key] !== undefined && payload[key] !== "" && payload[key] !== null) detail[key] = payload[key];
  }
  return Object.keys(detail).length ? JSON.stringify(detail, null, 2) : "";
}

function showApproval(payload) {
  els.approvalPanel.classList.remove("hidden");
  els.approvalQuestion.textContent = payload.question || "需要你的确认";
  els.approvalChoices.innerHTML = "";
  for (const choice of payload.choices || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = choice;
    button.addEventListener("click", () => {
      els.approvalInput.value = choice;
      els.approvalInput.focus();
    });
    els.approvalChoices.append(button);
  }
}

function clearApproval() {
  els.approvalPanel.classList.add("hidden");
  els.approvalQuestion.textContent = "";
  els.approvalChoices.innerHTML = "";
  els.approvalInput.value = "";
}

els.newSession.addEventListener("click", async () => {
  const session = await api("/api/sessions", { method: "POST" });
  await loadSessions(false);
  await selectSession(session.id);
});

els.refreshSessions.addEventListener("click", () => loadSessions(false).catch(console.error));

els.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.sessionId) {
    const session = await api("/api/sessions", { method: "POST" });
    await loadSessions(false);
    await selectSession(session.id);
  }
  const message = els.messageInput.value.trim();
  if (!message) return;
  addMessage("user", message);
  els.messageInput.value = "";
  setRunStatus("running");
  try {
    const run = await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    connectEvents(run.web_run_id);
  } catch (error) {
    setRunStatus("error");
    appendEvent("client_error", { created_at: new Date().toISOString(), error_type: "request_failed", result: error.message });
  }
});

els.stopRun.addEventListener("click", async () => {
  if (!state.activeRunId) return;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(state.activeRunId)}/abort`, { method: "POST" });
    setRunStatus(run.status);
  } catch (error) {
    appendEvent("client_error", { created_at: new Date().toISOString(), error_type: "abort_failed", result: error.message });
  }
});

els.sendApproval.addEventListener("click", async () => {
  const answer = els.approvalInput.value.trim();
  if (!answer || !state.activeRunId) return;
  await api(`/api/runs/${encodeURIComponent(state.activeRunId)}/approval`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
  clearApproval();
  setRunStatus("running");
});

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

loadSessions(true).catch((error) => {
  appendEvent("client_error", { created_at: new Date().toISOString(), error_type: "startup_failed", result: error.message });
});
