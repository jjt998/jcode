const state = {
  projects: [],
  projectId: "",
  sessions: [],
  sessionId: "",
  activeRunId: "",
  activeTurnId: "",
  turns: [],
  eventIds: new Set(),
  eventSource: null,
};

const els = {
  projectList: document.querySelector("#projectList"),
  projectForm: document.querySelector("#projectForm"),
  projectPath: document.querySelector("#projectPath"),
  projectName: document.querySelector("#projectName"),
  sessionList: document.querySelector("#sessionList"),
  newSession: document.querySelector("#newSession"),
  refreshAll: document.querySelector("#refreshAll"),
  sessionTitle: document.querySelector("#sessionTitle"),
  projectRoot: document.querySelector("#projectRoot"),
  runState: document.querySelector("#runState"),
  turnList: document.querySelector("#turnList"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  stopRun: document.querySelector("#stopRun"),
};

const STREAM_EVENTS = [
  "web_run_started",
  "jcode_run_bound",
  "run_started",
  "context_built",
  "model_requested",
  "model_responded",
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

async function loadProjects(selectLatest = false) {
  state.projects = await api("/api/projects");
  renderProjects();
  if (!state.projectId && state.projects.length && selectLatest) {
    await selectProject(state.projects[0].id);
  }
}

function renderProjects() {
  els.projectList.innerHTML = "";
  if (!state.projects.length) {
    els.projectList.append(emptyNode("还没有项目"));
    return;
  }
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.className = "project-item";
    if (project.id === state.projectId) button.classList.add("active");
    button.innerHTML = `
      <span class="project-name">${escapeHtml(project.name)}</span>
      <span class="project-root">${escapeHtml(project.root)}</span>
      <span class="project-meta">${project.session_count || 0} sessions${project.has_git ? " · git" : ""}</span>
    `;
    button.addEventListener("click", () => selectProject(project.id));
    els.projectList.append(button);
  }
}

async function selectProject(projectId) {
  const project = await api(`/api/projects/${encodeURIComponent(projectId)}`);
  state.projectId = project.id;
  state.sessionId = "";
  state.activeRunId = "";
  state.activeTurnId = "";
  state.turns = [];
  els.projectRoot.textContent = project.root;
  els.sessionTitle.textContent = project.name;
  setRunStatus("idle");
  stopStream();
  renderProjects();
  renderTurns();
  await loadSessions(true);
}

async function loadSessions(selectLatest = false) {
  if (!state.projectId) return;
  state.sessions = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions`);
  renderSessions();
  if (!state.sessionId && state.sessions.length && selectLatest) {
    await selectSession(state.sessions[0].id);
  }
}

function renderSessions() {
  els.sessionList.innerHTML = "";
  if (!state.sessions.length) {
    els.sessionList.append(emptyNode(state.projectId ? "这个项目还没有会话" : "先选择项目"));
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
  const session = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions/${encodeURIComponent(sessionId)}`);
  state.sessionId = session.id;
  state.activeRunId = session.active_run_id || "";
  state.activeTurnId = "";
  els.sessionTitle.textContent = session.id;
  els.projectRoot.textContent = session.project_root || "";
  renderSessions();
  setRunStatus(session.active_status || "idle");
  await loadTurns();
  if (state.activeRunId) connectEvents(state.activeRunId);
}

async function loadTurns() {
  if (!state.projectId || !state.sessionId) return;
  const data = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions/${encodeURIComponent(state.sessionId)}/turns`);
  state.turns = data.turns || [];
  renderTurns();
}

function renderTurns() {
  els.turnList.innerHTML = "";
  if (!state.turns.length) {
    const node = document.createElement("div");
    node.className = "empty-state";
    node.innerHTML = "<strong>准备开始</strong><span>发送一个任务，推理过程会折叠在这个 turn 里。</span>";
    els.turnList.append(node);
    return;
  }
  for (const turn of state.turns) {
    els.turnList.append(renderTurn(turn));
  }
  els.turnList.scrollTop = els.turnList.scrollHeight;
}

function renderTurn(turn) {
  const item = document.createElement("article");
  item.className = "turn";
  item.dataset.turnId = turn.local_id || turn.run_id;
  if (turn.user_message) item.append(messageNode("user", turn.user_message));
  if ((turn.events && turn.events.length) || turn.status !== "history") item.append(reasoningDrawer(turn));
  if (turn.pending_approval) item.append(approvalNode(turn));
  if (turn.assistant_message) item.append(messageNode("assistant", turn.assistant_message));
  return item;
}

function messageNode(role, content) {
  const node = document.createElement("section");
  node.className = `message ${role}`;
  node.innerHTML = `<span>${escapeHtml(role)}</span><p>${escapeHtml(content)}</p>`;
  return node;
}

function reasoningDrawer(turn) {
  const details = document.createElement("details");
  details.className = "reasoning";
  details.innerHTML = `<summary>${escapeHtml(reasoningTitle(turn))}</summary>`;
  const events = document.createElement("div");
  events.className = "reasoning-events";
  if (turn.events && turn.events.length) {
    for (const event of turn.events || []) {
      events.append(reasoningEvent(event));
    }
  } else {
    const empty = document.createElement("div");
    empty.className = "reasoning-empty";
    empty.textContent = "等待第一个事件";
    events.append(empty);
  }
  details.append(events);
  return details;
}

function reasoningEvent(event) {
  const details = document.createElement("details");
  details.className = `reasoning-event event-${event.event || "message"}`;
  const title = eventTitle(event);
  const content = eventContent(event);
  details.innerHTML = `
    <summary>
      <span>${escapeHtml(title)}</span>
      <small>${escapeHtml(formatTime(event.created_at))}</small>
    </summary>
    <pre>${escapeHtml(content || "这个历史事件没有保存完整内容")}</pre>
  `;
  return details;
}

function approvalNode(turn) {
  const section = document.createElement("section");
  section.className = "approval";
  const choices = (turn.pending_choices || [])
    .map((choice) => `<button type="button" data-choice="${escapeHtml(choice)}">${escapeHtml(choice)}</button>`)
    .join("");
  section.innerHTML = `
    <div>
      <p class="eyebrow">approval</p>
      <h3>${escapeHtml(turn.pending_question || "需要你的确认")}</h3>
      <div class="choices">${choices}</div>
    </div>
    <div class="approval-actions">
      <input placeholder="输入你的回复" />
      <button type="button">继续</button>
    </div>
  `;
  const input = section.querySelector("input");
  section.querySelectorAll(".choices button").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.choice || "";
      input.focus();
    });
  });
  section.querySelector(".approval-actions button").addEventListener("click", async () => {
    const answer = input.value.trim();
    if (!answer || !state.activeRunId) return;
    await api(`/api/runs/${encodeURIComponent(state.activeRunId)}/approval`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    });
    turn.pending_approval = false;
    turn.pending_question = "";
    turn.pending_choices = [];
    turn.status = "running";
    setRunStatus("running");
    renderTurns();
  });
  return section;
}

function reasoningTitle(turn) {
  const status = statusLabel(turn.status);
  const eventCount = turn.events ? turn.events.length : turn.event_count || 0;
  const toolCount = countTools(turn);
  const changedFiles = collectChangedFiles(turn.events || []);
  const parts = [`${status} · ${eventCount} events`];
  if (toolCount) parts.push(`${toolCount} tools`);
  if (changedFiles.length) parts.push(`${changedFiles.length} file${changedFiles.length > 1 ? "s" : ""} changed`);
  return parts.join(" · ");
}

function countTools(turn) {
  return (turn.events || []).filter((event) => event.event === "tool_executed").length || turn.tool_count || 0;
}

function collectChangedFiles(events) {
  const files = new Set();
  for (const event of events) {
    for (const file of event.changed_files || []) files.add(file);
  }
  return [...files];
}

function statusLabel(status) {
  const labels = {
    running: "推理中",
    waiting_approval: "等待确认",
    completed: "已完成",
    stopped: "已停止",
    aborted: "已停止",
    aborting: "停止中",
    failed: "失败",
    incomplete: "未完成",
    history: "历史消息",
  };
  return labels[status] || status || "推理中";
}

function eventTitle(event) {
  const name = event.event || "message";
  const tool = event.name || event.tool_name;
  const labels = {
    web_run_started: "开始运行",
    jcode_run_bound: "绑定 Run",
    run_started: "运行开始",
    context_built: "Context 拼凑",
    model_requested: "模型请求",
    model_responded: "模型原始返回",
    model_parsed: "模型解析结果",
    tool_requested: `工具请求${tool ? `: ${tool}` : ""}`,
    tool_executed: `工具结果${tool ? `: ${tool}` : ""}`,
    subagent_completed: `子任务结果${tool ? `: ${tool}` : ""}`,
    checkpoint_created: "Checkpoint",
    final_readiness_decision: "Final gate",
    memory_maintained: "记忆整理",
    run_finished: "运行结束",
    approval_required: "等待确认",
    approval_answered: "已确认",
    web_run_completed: "最终回答",
    run_abort_requested: "请求停止",
    run_aborted: "已停止",
    run_failed: "失败",
  };
  return labels[name] || name;
}

function eventContent(event) {
  if (event.event === "context_built") return event.context || fallbackJson(event);
  if (event.event === "model_responded") return event.response_text || fallbackJson(event);
  if (event.event === "model_parsed") return formatJson(event.action || event);
  if (event.event === "tool_requested") return formatJson(event.args || event);
  if (event.event === "tool_executed" || event.event === "subagent_completed") return event.result || fallbackJson(event);
  if (event.event === "web_run_completed") return event.final_text || fallbackJson(event);
  return fallbackJson(event);
}

function fallbackJson(event) {
  const copy = { ...event };
  delete copy.context;
  delete copy.response_text;
  delete copy.result;
  return formatJson(copy);
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function connectEvents(runId) {
  if (!runId) return;
  stopStream();
  state.activeRunId = runId;
  const source = new EventSource(`/api/projects/${encodeURIComponent(state.projectId)}/runs/${encodeURIComponent(runId)}/events`);
  state.eventSource = source;
  for (const name of STREAM_EVENTS) {
    source.addEventListener(name, (event) => handleRunEvent(name, event));
  }
  source.onerror = () => setRunStatus("disconnected");
}

function stopStream() {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = null;
  state.eventIds.clear();
}

function handleRunEvent(name, event) {
  const payload = JSON.parse(event.data);
  const eventId = payload.event_id || event.lastEventId || `${name}:${Date.now()}`;
  if (state.eventIds.has(eventId)) return;
  state.eventIds.add(eventId);

  const turn = activeTurn(payload);
  if (payload.run_id && !String(payload.run_id).startsWith("web-run-")) {
    turn.run_id = payload.run_id;
  }
  if (payload.web_run_id) {
    turn.web_run_id = payload.web_run_id;
    state.activeRunId = payload.web_run_id;
  }
  if (name === "jcode_run_bound" && payload.jcode_run_id) {
    turn.run_id = payload.jcode_run_id;
  }
  if (name === "approval_required") {
    turn.status = "waiting_approval";
    turn.pending_approval = true;
    turn.pending_question = payload.question || "";
    turn.pending_choices = payload.choices || [];
    setRunStatus("waiting_approval");
  } else if (name === "run_abort_requested") {
    turn.status = "aborting";
    setRunStatus("aborting");
  } else if (name === "run_aborted") {
    turn.status = "aborted";
    setRunStatus("aborted");
  } else if (name === "run_failed") {
    turn.status = "failed";
    setRunStatus("failed");
  } else if (name === "web_run_completed" || name === "run_finished") {
    turn.status = name === "run_finished" && payload.status !== "completed" ? "stopped" : "completed";
    setRunStatus(turn.status);
    if (payload.final_text) turn.assistant_message = payload.final_text;
    loadSessions(false).catch(console.error);
  } else if (name !== "stream_closed") {
    if (turn.status !== "waiting_approval") turn.status = "running";
    if (turn.status === "running") setRunStatus("running");
  }
  if (name === "approval_answered") {
    turn.pending_approval = false;
    turn.pending_question = "";
    turn.pending_choices = [];
  }
  if (name !== "stream_closed") {
    turn.events.push(payload);
  } else {
    stopStream();
  }
  renderTurns();
}

function activeTurn(payload = {}) {
  const ids = [payload.web_run_id, payload.jcode_run_id, payload.run_id, state.activeTurnId].filter(Boolean);
  let turn = state.turns.find((item) => ids.includes(item.web_run_id) || ids.includes(item.run_id) || ids.includes(item.local_id));
  if (!turn) {
    turn = {
      local_id: payload.web_run_id || state.activeTurnId || `pending-${Date.now()}`,
      web_run_id: payload.web_run_id || "",
      run_id: payload.jcode_run_id || payload.run_id || "",
      user_message: "",
      assistant_message: "",
      status: "running",
      events: [],
      pending_approval: false,
    };
    state.turns.push(turn);
  }
  return turn;
}

function emptyNode(text) {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = text;
  return empty;
}

els.projectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const root = els.projectPath.value.trim();
  if (!root) return;
  const project = await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({ root, name: els.projectName.value.trim() || null }),
  });
  els.projectPath.value = "";
  els.projectName.value = "";
  await loadProjects(false);
  await selectProject(project.id);
});

els.newSession.addEventListener("click", async () => {
  if (!state.projectId) return;
  const session = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions`, { method: "POST" });
  await loadSessions(false);
  await selectSession(session.id);
});

els.refreshAll.addEventListener("click", async () => {
  await loadProjects(false);
  await loadSessions(false);
  if (state.sessionId) await loadTurns();
});

els.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.projectId) return;
  if (!state.sessionId) {
    const session = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions`, { method: "POST" });
    await loadSessions(false);
    await selectSession(session.id);
  }
  const message = els.messageInput.value.trim();
  if (!message) return;
  const localId = `pending-${Date.now()}`;
  state.activeTurnId = localId;
  state.turns.push({
    local_id: localId,
    run_id: "",
    web_run_id: "",
    user_message: message,
    assistant_message: "",
    status: "running",
    events: [],
    pending_approval: false,
  });
  els.messageInput.value = "";
  setRunStatus("running");
  renderTurns();
  try {
    const run = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions/${encodeURIComponent(state.sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    const turn = activeTurn({ web_run_id: run.web_run_id });
    turn.web_run_id = run.web_run_id;
    turn.run_id = run.jcode_run_id || run.run_id || "";
    state.activeRunId = run.web_run_id;
    connectEvents(run.web_run_id);
  } catch (error) {
    const turn = activeTurn();
    turn.status = "failed";
    turn.events.push({ event: "client_error", created_at: new Date().toISOString(), error_type: "request_failed", result: error.message });
    setRunStatus("error");
    renderTurns();
  }
});

els.stopRun.addEventListener("click", async () => {
  if (!state.activeRunId) return;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(state.activeRunId)}/abort`, { method: "POST" });
    setRunStatus(run.status);
    const turn = activeTurn({ web_run_id: run.web_run_id, run_id: run.jcode_run_id || run.run_id });
    turn.status = run.status;
    renderTurns();
  } catch (error) {
    const turn = activeTurn();
    turn.events.push({ event: "client_error", created_at: new Date().toISOString(), error_type: "abort_failed", result: error.message });
    renderTurns();
  }
});

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

loadProjects(true).catch((error) => {
  state.turns = [
    {
      local_id: "startup-error",
      status: "failed",
      user_message: "",
      assistant_message: "",
      events: [{ event: "client_error", created_at: new Date().toISOString(), error_type: "startup_failed", result: error.message }],
    },
  ];
  renderTurns();
});
