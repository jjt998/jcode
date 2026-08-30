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
  openDetails: new Map(),
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
  "step_patch",
  "compact_evaluated",
  "compact_triggered",
  "compact_completed",
  "compact_fallback",
  "context_built",
  "model_requested",
  "model_responded",
  "model_parsed",
  "tool_requested",
  "tool_executed",
  "tool_sequence_requested",
  "tool_sequence_step_requested",
  "tool_sequence_completed",
  "tool_sequence_aborted",
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

function formatDuration(value) {
  if (value === null || value === undefined || value === "") return "";
  const ms = Number(value);
  if (Number.isNaN(ms)) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)}s`;
}

function setRunStatus(status) {
  els.runState.textContent = status || "idle";
  els.runState.dataset.status = status || "idle";
}

function summarizeText(text, limit = 20) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (!value) return "";
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

function previewText(text, limit = 60) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (!value) return "";
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
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
  state.openDetails.clear();
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
  state.openDetails.clear();
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
  state.turns = (data.turns || []).map(normalizeTurn);
  renderTurns();
}

function renderTurns() {
  els.turnList.innerHTML = "";
  if (!state.turns.length) {
    const node = document.createElement("div");
    node.className = "empty-state";
    node.innerHTML = "<strong>准备开始</strong><span>发送一个任务，步骤时间线会显示在这里。</span>";
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
  const steps = turn.reasoning_steps || [];
  if (steps.length || turn.reasoning_text || turn.status !== "history") item.append(stepTimeline(turn));
  if (turn.pending_approval) item.append(approvalNode(turn));
  if (turn.final_text || turn.assistant_message) item.append(finalAnswerNode(turn.final_text || turn.assistant_message));
  return item;
}

function messageNode(role, content) {
  const node = document.createElement("section");
  node.className = `message ${role}`;
  node.innerHTML = `<span>${escapeHtml(role)}</span><p>${escapeHtml(content)}</p>`;
  return node;
}

function finalAnswerNode(content) {
  const node = document.createElement("section");
  node.className = "final-answer";
  node.innerHTML = `
    <div class="final-head">
      <span class="eyebrow">最终答案</span>
      <span class="final-chip">完成</span>
    </div>
    <pre>${escapeHtml(content)}</pre>
  `;
  return node;
}

function stepTimeline(turn) {
  const section = document.createElement("section");
  section.className = "step-timeline";
  const steps = turn.reasoning_steps || [];
  if (!steps.length) {
    const empty = document.createElement("div");
    empty.className = "step-empty";
    empty.textContent = "等待步骤生成";
    section.append(empty);
    return section;
  }
  for (const step of steps) {
    section.append(stepItem(turn, step));
  }
  return section;
}

function stepItem(turn, step) {
  const details = document.createElement("details");
  details.className = `step step-${escapeHtml(step.status || "pending")}`;
  rememberOpenState(details, `step:${turnKey(turn)}:${step.step_id}`, false);
  details.innerHTML = `
    <summary>
      <div class="step-summary">
        <div class="step-summary-top">
          <span class="step-index">步骤 ${escapeHtml(String(step.index || 1))}</span>
          <span class="step-time">${escapeHtml(formatTime(step.timestamp))}</span>
          <span class="step-status">${escapeHtml(stepStatusLabel(step.status))}</span>
          <span class="step-meta">${escapeHtml(stepMetaLabel(step))}</span>
        </div>
        <div class="step-summary-text">${escapeHtml(step.reasoning_summary || summarizeText(step.reasoning_text || "没有推理内容"))}</div>
      </div>
    </summary>
  `;
  const body = document.createElement("div");
  body.className = "step-body";
  if (step.context_text) {
    body.append(detailBlock("Context 拼凑", step.context_text, `step-detail:${turnKey(turn)}:${step.step_id}:context`));
  }
  if (step.reasoning_text) {
    const reasoning = document.createElement("section");
    reasoning.className = "step-reasoning";
    reasoning.innerHTML = `
      <span class="eyebrow">推理内容</span>
      <pre>${escapeHtml(step.reasoning_text)}</pre>
    `;
    body.append(reasoning);
  }
  if (step.tool_calls && step.tool_calls.length) {
    const tools = document.createElement("section");
    tools.className = "step-tools";
    tools.innerHTML = `<div class="step-tools-head"><span class="eyebrow">工具调用清单</span><span>${escapeHtml(String(step.tool_calls.length))} 个</span></div>`;
    for (const [index, tool] of (step.tool_calls || []).entries()) {
      tools.append(toolCallNode(turn, step, tool, index));
    }
    body.append(tools);
  }
  for (const detail of step.details || []) {
    if (detail.event === "context_built" || detail.event === "model_responded" || detail.event === "model_parsed") continue;
    body.append(detailBlock(detail.title || detail.event, detail.content || "", `step-detail:${turnKey(turn)}:${step.step_id}:${detail.event}:${detail.created_at || ""}`));
  }
  details.append(body);
  return details;
}

function toolCallNode(turn, step, tool, index) {
  const details = document.createElement("details");
  details.className = `tool-call tool-${escapeHtml(tool.status || "running")}`;
  rememberOpenState(details, `tool:${turnKey(turn)}:${step.step_id}:${tool.tool_id}`, false);
  details.innerHTML = `
    <summary>
      <div class="tool-summary">
        <span class="tool-name">🔧 ${escapeHtml(tool.name || `tool-${index + 1}`)}</span>
        <span class="tool-args">${escapeHtml(previewText(tool.args_text || "", 60))}</span>
      </div>
      <div class="tool-summary-meta">
        <span class="tool-status">${escapeHtml(toolStatusLabel(tool.status))}</span>
        <span class="tool-duration">${escapeHtml(formatDuration(tool.duration_ms))}</span>
      </div>
    </summary>
  `;
  const body = document.createElement("div");
  body.className = "tool-body";
  body.append(detailBlock("参数", tool.args_text || "{}", `tool-args:${turnKey(turn)}:${step.step_id}:${tool.tool_id}`));
  if (tool.result_text) {
    body.append(detailBlock("返回结果", tool.result_text, `tool-result:${turnKey(turn)}:${step.step_id}:${tool.tool_id}`));
  }
  details.append(body);
  return details;
}

function detailBlock(title, content, key) {
  const details = document.createElement("details");
  details.className = "mini-detail";
  rememberOpenState(details, key, false);
  details.innerHTML = `
    <summary><span>${escapeHtml(title)}</span></summary>
    <pre>${escapeHtml(content || "这个历史事件没有保存完整内容")}</pre>
  `;
  return details;
}

function rememberOpenState(details, key, defaultOpen = false) {
  const remembered = state.openDetails.has(key) ? state.openDetails.get(key) : defaultOpen;
  details.open = Boolean(remembered);
  details.addEventListener("toggle", () => {
    state.openDetails.set(key, details.open);
  });
}

function turnKey(turn) {
  return `${state.sessionId || "session"}:${turn.local_id || turn.web_run_id || turn.run_id || ""}`;
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

function stepMetaLabel(step) {
  const parts = [];
  const toolCount = (step.tool_calls || []).length;
  parts.push(`工具 ${toolCount} 个`);
  const duration = formatDuration(step.duration_ms);
  if (duration) parts.push(`耗时 ${duration}`);
  return parts.join(" · ");
}

function stepStatusLabel(status) {
  const labels = {
    pending: "待执行",
    running: "执行中",
    success: "已完成",
    error: "失败",
    timeout: "超时",
  };
  return labels[status] || status || "待执行";
}

function toolStatusLabel(status) {
  const labels = {
    pending: "待执行",
    running: "执行中",
    success: "成功",
    error: "失败",
    timeout: "超时",
  };
  return labels[status] || status || "执行中";
}

function normalizeTurn(turn) {
  if (!turn) return turn;
  if (!Array.isArray(turn.reasoning_steps)) turn.reasoning_steps = [];
  if (!turn.final_text) turn.final_text = turn.assistant_message || "";
  if (!turn.stepMap || !(turn.stepMap instanceof Map)) {
    turn.stepMap = new Map();
    for (const step of turn.reasoning_steps) {
      if (step && step.step_id) turn.stepMap.set(step.step_id, step);
    }
  }
  if (!turn.reasoning_steps.length && turn.reasoning_text) {
    const synthetic = syntheticStep(turn);
    turn.reasoning_steps = [synthetic];
    turn.stepMap.set(synthetic.step_id, synthetic);
  }
  return turn;
}

function syntheticStep(turn) {
  return {
    step_id: `${turn.run_id || turn.local_id || "turn"}:1`,
    index: 1,
    timestamp: "",
    end_timestamp: "",
    status: turn.status === "failed" ? "error" : "success",
    reasoning_text: turn.reasoning_text || "",
    reasoning_summary: summarizeText(turn.reasoning_text || "", 20),
    context_text: "",
    response_text: "",
    parsed_action: {},
    tool_calls: [],
    details: [],
    duration_ms: null,
    tool_count: 0,
  };
}

function upsertStep(turn, step) {
  normalizeTurn(turn);
  if (!step || !step.step_id) return;
  turn.stepMap.set(step.step_id, step);
  turn.reasoning_steps = [...turn.stepMap.values()].sort(compareSteps);
}

function compareSteps(a, b) {
  const ai = Number(a.index || 0);
  const bi = Number(b.index || 0);
  if (ai !== bi) return ai - bi;
  const at = String(a.timestamp || "");
  const bt = String(b.timestamp || "");
  if (at !== bt) return at < bt ? -1 : 1;
  return String(a.step_id || "").localeCompare(String(b.step_id || ""));
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
  if (name === "step_patch" && payload.step) {
    upsertStep(turn, payload.step);
    renderTurns();
    return;
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
    if (payload.final_text) {
      turn.final_text = payload.final_text;
      turn.assistant_message = payload.final_text;
    }
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
  if (turn) return normalizeTurn(turn);
  turn = normalizeTurn({
    local_id: payload.web_run_id || state.activeTurnId || `pending-${Date.now()}`,
    web_run_id: payload.web_run_id || "",
    run_id: payload.jcode_run_id || payload.run_id || "",
    user_message: "",
    reasoning_text: "",
    reasoning_steps: [],
    final_text: "",
    assistant_message: "",
    status: "running",
    events: [],
    pending_approval: false,
    stepMap: new Map(),
  });
  state.turns.push(turn);
  return turn;
}

function emptyNode(text) {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = text;
  return empty;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function detailBlock(title, content, key) {
  const details = document.createElement("details");
  details.className = "mini-detail";
  rememberOpenState(details, key, false);
  details.innerHTML = `
    <summary><span>${escapeHtml(title)}</span></summary>
    <pre>${escapeHtml(content || "这个历史事件没有保存完整内容")}</pre>
  `;
  return details;
}

function rememberOpenState(details, key, defaultOpen = false) {
  const remembered = state.openDetails.has(key) ? state.openDetails.get(key) : defaultOpen;
  details.open = Boolean(remembered);
  details.addEventListener("toggle", () => {
    state.openDetails.set(key, details.open);
  });
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
  state.turns.push(
    normalizeTurn({
      local_id: localId,
      run_id: "",
      web_run_id: "",
      user_message: message,
      reasoning_text: "",
      reasoning_steps: [],
      final_text: "",
      assistant_message: "",
      status: "running",
      events: [],
      pending_approval: false,
      stepMap: new Map(),
    }),
  );
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

loadProjects(true).catch((error) => {
  state.turns = [
    normalizeTurn({
      local_id: "startup-error",
      status: "failed",
      user_message: "",
      reasoning_text: "",
      reasoning_steps: [],
      final_text: "",
      assistant_message: "",
      events: [{ event: "client_error", created_at: new Date().toISOString(), error_type: "startup_failed", result: error.message }],
      stepMap: new Map(),
    }),
  ];
  renderTurns();
});
