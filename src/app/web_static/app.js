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
  reconnectTimer: null,
  selectionEpoch: 0,
  openDetails: new Map(),
  modelProfiles: [],
  followLatest: true,
  programmaticScroll: false,
  newContentPending: false,
  renderFrame: 0,
};

const els = {
  projectList: document.querySelector("#projectList"),
  projectForm: document.querySelector("#projectForm"),
  projectPath: document.querySelector("#projectPath"),
  projectName: document.querySelector("#projectName"),
  sessionList: document.querySelector("#sessionList"),
  sessionPanel: document.querySelector("#sessionPanel"),
  sessionToggle: document.querySelector("#sessionToggle"),
  sidebarResizer: document.querySelector("#sidebarResizer"),
  topbarResizer: document.querySelector("#topbarResizer"),
  composerResizer: document.querySelector("#composerResizer"),
  newSession: document.querySelector("#newSession"),
  refreshAll: document.querySelector("#refreshAll"),
  sessionTitle: document.querySelector("#sessionTitle"),
  projectRoot: document.querySelector("#projectRoot"),
  runState: document.querySelector("#runState"),
  turnList: document.querySelector("#turnList"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  sendMessage: document.querySelector("#sendMessage"),
  stopRun: document.querySelector("#stopRun"),
  modelProfile: document.querySelector("#modelProfile"),
  reasoningToggleField: document.querySelector("#reasoningToggleField"),
  thinkingEnabled: document.querySelector("#thinkingEnabled"),
  reasoningEffort: document.querySelector("#reasoningEffort"),
  reasoningSupport: document.querySelector("#reasoningSupport"),
  jumpLatest: document.querySelector("#jumpLatest"),
};

const FOLLOW_LATEST_THRESHOLD = 24;

function isAtLatest() {
  return els.turnList.scrollHeight - els.turnList.scrollTop - els.turnList.clientHeight <= FOLLOW_LATEST_THRESHOLD;
}

els.turnList.addEventListener("scroll", () => {
  if (state.programmaticScroll) return;
  state.followLatest = isAtLatest();
  if (state.followLatest) {
    state.newContentPending = false;
    els.jumpLatest.hidden = true;
  }
});

els.jumpLatest.addEventListener("click", () => {
  state.followLatest = true;
  state.newContentPending = false;
  els.jumpLatest.hidden = true;
  state.programmaticScroll = true;
  els.turnList.scrollTo({ top: els.turnList.scrollHeight, behavior: "smooth" });
  window.setTimeout(() => {
    state.programmaticScroll = false;
    els.turnList.scrollTop = els.turnList.scrollHeight;
  }, 220);
});

// 侧栏宽度只影响布局，不改变项目、会话或运行状态。
const SIDEBAR_MIN_WIDTH = 240;
const SIDEBAR_MAX_WIDTH = 520;
let resizingSidebar = false;

function setSidebarWidth(width) {
  const nextWidth = Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, width));
  document.querySelector(".shell").style.setProperty("--sidebar-width", `${Math.round(nextWidth)}px`);
  els.sidebarResizer.setAttribute("aria-valuenow", String(Math.round(nextWidth)));
}

els.sidebarResizer.setAttribute("aria-valuemin", String(SIDEBAR_MIN_WIDTH));
els.sidebarResizer.setAttribute("aria-valuemax", String(SIDEBAR_MAX_WIDTH));

els.sidebarResizer.addEventListener("pointerdown", (event) => {
  resizingSidebar = true;
  els.sidebarResizer.setPointerCapture(event.pointerId);
  els.sidebarResizer.classList.add("dragging");
  document.body.classList.add("resizing-sidebar");
});
els.sidebarResizer.addEventListener("pointermove", (event) => {
  if (!resizingSidebar) return;
  setSidebarWidth(event.clientX);
});
els.sidebarResizer.addEventListener("pointerup", () => {
  resizingSidebar = false;
  els.sidebarResizer.classList.remove("dragging");
  document.body.classList.remove("resizing-sidebar");
});
els.sidebarResizer.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  const current = parseInt(getComputedStyle(document.querySelector(".shell")).getPropertyValue("--sidebar-width"), 10) || 280;
  setSidebarWidth(current + (event.key === "ArrowRight" ? 16 : -16));
});

// 标题区和输入区的高度独立调整，中间消息区自动占据剩余空间。
function bindHorizontalResizer(element, variableName, minHeight, maxHeight, direction) {
  let resizing = false;
  const shell = document.querySelector(".conversation");
  const getCurrent = () => parseInt(getComputedStyle(shell).getPropertyValue(variableName), 10) || minHeight;
  const setHeight = (height) => {
    const nextHeight = Math.max(minHeight, Math.min(maxHeight, height));
    shell.style.setProperty(variableName, `${Math.round(nextHeight)}px`);
    element.setAttribute("aria-valuenow", String(Math.round(nextHeight)));
  };
  element.setAttribute("aria-valuemin", String(minHeight));
  element.setAttribute("aria-valuemax", String(maxHeight));
  element.addEventListener("pointerdown", (event) => {
    resizing = true;
    element.setPointerCapture(event.pointerId);
    element.classList.add("dragging");
    document.body.classList.add("resizing-horizontal");
  });
  element.addEventListener("pointermove", (event) => {
    if (!resizing) return;
    const box = shell.getBoundingClientRect();
    const height = direction === "top" ? event.clientY - box.top : box.bottom - event.clientY;
    setHeight(height);
  });
  element.addEventListener("pointerup", () => {
    resizing = false;
    element.classList.remove("dragging");
    document.body.classList.remove("resizing-horizontal");
  });
  element.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const delta = event.key === "ArrowUp" ? -16 : 16;
    setHeight(getCurrent() + (direction === "top" ? delta : -delta));
  });
}

bindHorizontalResizer(els.topbarResizer, "--topbar-height", 76, 220, "top");
bindHorizontalResizer(els.composerResizer, "--composer-height", 150, 360, "bottom");

// 会话区默认收起，展开后会话列表在固定区域内滚动。
els.sessionToggle.addEventListener("click", () => {
  const expanded = els.sessionToggle.getAttribute("aria-expanded") === "true";
  els.sessionToggle.setAttribute("aria-expanded", String(!expanded));
  els.sessionPanel.hidden = expanded;
  els.sessionToggle.querySelector(".toggle-mark").textContent = expanded ? "+" : "−";
});

// Web 只订阅明确允许展示的事件；新增后端事件不会自动进入前端。
const STREAM_EVENTS = Object.freeze([
  "web_run_started",
  "jcode_run_bound",
  "run_started",
  "step_patch",
  "compact_evaluated",
  "compact_triggered",
  "compact_completed",
  "compact_fallback",
  "context_compression_compared",
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
  "runtime_stopped",
  "stream_closed",
]);

// 步骤详情同样采用白名单，避免未知事件落入通用兜底后被意外展示。
const DISPLAYABLE_DETAIL_EVENTS = new Set([
  "context_built",
  "tool_requested",
  "tool_executed",
  "subagent_completed",
  "checkpoint_created",
  "final_readiness_decision",
  "final_readiness_evaluated",
  "final_gate_rerun_requested",
  "memory_maintained",
  "run_finished",
  "approval_required",
  "approval_answered",
  "web_run_completed",
  "run_abort_requested",
  "run_aborted",
  "run_failed",
  "runtime_stopped",
]);

const HIDDEN_REASON_DETAIL_EVENTS = new Set([
  "model_requested",
  "tool_sequence_requested",
  "tool_sequence_step_requested",
  "tool_sequence_completed",
  "tool_sequence_aborted",
  "tool_requested",
  "tool_executed",
  "checkpoint_created",
]);

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
  const labels = {
    idle: "空闲",
    running: "运行中",
    waiting_approval: "等待确认",
    aborting: "正在停止",
    completed: "已完成",
    finished: "已完成",
    failed: "已失败",
    aborted: "已中止",
    error: "连接错误",
  };
  els.runState.textContent = labels[status] || status || "空闲";
  els.runState.dataset.status = status || "idle";
  els.modelProfile.disabled = ["running", "waiting_approval", "aborting"].includes(status);
  const running = els.modelProfile.disabled;
  els.messageInput.disabled = running;
  els.sendMessage.disabled = running;
  els.thinkingEnabled.disabled = running || els.thinkingEnabled.dataset.locked === "true" || els.thinkingEnabled.dataset.supported !== "true";
  els.reasoningEffort.disabled = running || els.reasoningEffort.dataset.supported !== "true" || els.thinkingEnabled.checked === false;
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
  const epoch = ++state.selectionEpoch;
  stopStream();
  const project = await api(`/api/projects/${encodeURIComponent(projectId)}`);
  if (epoch !== state.selectionEpoch) return;
  state.projectId = project.id;
  state.sessionId = "";
  state.activeRunId = "";
  state.activeTurnId = "";
  state.turns = [];
  state.followLatest = true;
  state.newContentPending = false;
  state.openDetails.clear();
  els.projectRoot.textContent = project.root;
  els.sessionTitle.textContent = project.name;
  setRunStatus("idle");
  renderProjects();
  renderTurns({ initial: true });
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
  const epoch = ++state.selectionEpoch;
  // 切换窗口时立即关闭旧流，避免旧会话的尾部事件落入新窗口。
  stopStream();
  state.turns = [];
  state.followLatest = true;
  state.newContentPending = false;
  state.openDetails.clear();
  renderTurns({ initial: true });
  const session = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions/${encodeURIComponent(sessionId)}`);
  if (epoch !== state.selectionEpoch) return;
  state.sessionId = session.id;
  state.activeRunId = session.active_run_id || "";
  state.activeTurnId = "";
  state.openDetails.clear();
  els.sessionTitle.textContent = session.id;
  els.projectRoot.textContent = session.project_root || "";
  renderSessions();
  setRunStatus(session.active_status || "idle");
  state.modelProfiles = session.model_profiles || [];
  renderModelProfiles(state.modelProfiles, session.active_model_profile || "");
  await loadTurns(epoch);
  if (state.activeRunId) connectEvents(state.activeRunId);
}

function renderModelProfiles(profiles, selected) {
  els.modelProfile.innerHTML = "";
  for (const profile of profiles) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.model;
    option.selected = profile.id === selected;
    els.modelProfile.append(option);
  }
  updateReasoningControls(profiles, selected);
}

function updateReasoningControls(profiles, selected) {
  const profile = profiles.find((item) => item.id === selected);
  const supported = Boolean(profile && profile.reasoning_mode !== "none");
  const alwaysOn = Boolean(profile?.reasoning_always_on);
  els.reasoningEffort.dataset.supported = supported ? "true" : "false";
  els.thinkingEnabled.dataset.supported = supported ? "true" : "false";
  els.thinkingEnabled.dataset.locked = alwaysOn ? "true" : "false";
  els.thinkingEnabled.checked = supported && Boolean(profile?.thinking_enabled);
  els.reasoningToggleField.hidden = !supported;
  els.reasoningEffort.innerHTML = "";
  for (const effort of (profile?.reasoning_effort_options || [])) {
    const option = document.createElement("option");
    option.value = effort;
    option.textContent = effort;
    option.selected = effort === profile.reasoning_effort;
    els.reasoningEffort.append(option);
  }
  els.reasoningSupport.textContent = !supported ? "该模型不支持推理" : alwaysOn ? "该模型始终开启" : "下一轮生效";
  setRunStatus(els.runState.dataset.status || "idle");
}

async function loadTurns(epoch = state.selectionEpoch) {
  if (!state.projectId || !state.sessionId) return;
  const projectId = state.projectId;
  const sessionId = state.sessionId;
  const data = await api(`/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/turns`);
  if (epoch !== state.selectionEpoch || projectId !== state.projectId || sessionId !== state.sessionId) return;
  state.turns = (data.turns || []).map(normalizeTurn);
  if (data.active_run && data.active_run.web_run_id) {
    const active = data.active_run;
    let turn = state.turns.find((item) => item.web_run_id === active.web_run_id || item.run_id === active.jcode_run_id);
    if (!turn) {
      turn = normalizeTurn({
        local_id: active.web_run_id,
        web_run_id: active.web_run_id,
        run_id: active.jcode_run_id || active.run_id || "",
        user_message: active.user_message || "",
        reasoning_steps: active.reasoning_steps || [],
        final_text: "",
        assistant_message: "",
        status: active.status || "running",
        events: [],
        pending_approval: Boolean(active.pending_question),
        pending_question: active.pending_question || "",
        pending_choices: active.pending_choices || [],
        event_cursor: Number(active.event_cursor || 0),
        stepMap: new Map((active.reasoning_steps || []).filter((step) => step && step.step_id).map((step) => [step.step_id, step])),
      });
      state.turns.push(turn);
    } else {
      turn.reasoning_steps = active.reasoning_steps || turn.reasoning_steps || [];
      turn.stepMap = new Map(turn.reasoning_steps.filter((step) => step && step.step_id).map((step) => [step.step_id, step]));
      turn.event_cursor = Number(active.event_cursor || turn.event_cursor || 0);
      turn.status = active.status || turn.status;
    }
    // active run 的过程型 assistant 文本不能沿用历史 turn 的最终答案兜底。
    if (["running", "waiting_approval", "aborting"].includes(active.status)) {
      turn.final_text = "";
      turn.assistant_message = "";
    } else if (active.final_text) {
      turn.final_text = active.final_text;
      turn.assistant_message = active.final_text;
    }
    state.activeTurnId = turn.local_id;
  }
  sortTurns();
  renderTurns({ initial: true });
}

function renderTurns(options = {}) {
  if (options.initial) {
    state.followLatest = true;
    state.newContentPending = false;
  }
  const scrollState = options.initial ? { followBottom: true } : captureTurnScrollState();
  const detailState = captureDetailState();
  if (!state.turns.length) {
    if (!els.turnList.querySelector(".empty-state")) {
      const node = document.createElement("div");
      node.className = "empty-state";
      node.innerHTML = "<strong>准备开始</strong><span>发送一个任务，步骤时间线会显示在这里。</span>";
      turnNodes().forEach((turn) => turn.remove());
      els.turnList.insertBefore(node, els.jumpLatest);
    }
    els.jumpLatest.hidden = true;
    return;
  }

  els.turnList.querySelector(".empty-state")?.remove();
  const existingTurns = new Map(turnNodes().map((node) => [node.dataset.turnId, node]));
  const desiredNodes = [];
  for (const turn of state.turns) {
    const turnId = renderTurnId(turn);
    const signature = turnRenderSignature(turn);
    const existing = existingTurns.get(turnId);
    const node = existing?.dataset.renderSignature === signature ? existing : existing ? patchTurn(existing, turn) : renderTurn(turn);
    node.dataset.renderSignature = signature;
    desiredNodes.push(node);
  }
  // 先移动目标节点到正确顺序，再删除旧节点，避免内容变化时残留同一轮次的副本。
  for (const node of desiredNodes) els.turnList.append(node);
  const desiredNodeSet = new Set(desiredNodes);
  for (const node of turnNodes()) {
    if (!desiredNodeSet.has(node)) node.remove();
  }
  restoreDetailState(detailState);
  restoreTurnScrollState(scrollState);
  els.jumpLatest.hidden = state.followLatest || !state.newContentPending;
}

function patchTurn(existing, turn) {
  const fresh = renderTurn(turn);
  morphNode(existing, fresh);
  return existing;
}

function morphNode(existing, fresh) {
  if (existing.nodeType === Node.TEXT_NODE && fresh.nodeType === Node.TEXT_NODE) {
    if (existing.nodeValue !== fresh.nodeValue) existing.nodeValue = fresh.nodeValue;
    return existing;
  }
  if (existing.nodeType !== fresh.nodeType || existing.nodeName !== fresh.nodeName) return fresh;
  const wasOpen = existing instanceof HTMLDetailsElement ? existing.open : null;
  const scrollTop = existing.scrollTop;
  for (const attribute of [...existing.attributes]) {
    if (!fresh.hasAttribute(attribute.name)) existing.removeAttribute(attribute.name);
  }
  for (const attribute of [...fresh.attributes]) existing.setAttribute(attribute.name, attribute.value);
  // 动态加载的正文由用户当前视图拥有，实时快照不能用占位内容覆盖它。
  if (existing.dataset.contentLoaded === "true") return existing;
  const oldChildren = [...existing.childNodes];
  const freshChildren = [...fresh.childNodes];
  const used = new Set();
  const keyed = new Map(oldChildren.map((child) => [child.dataset?.stateKey || child.dataset?.turnId || "", child]).filter(([key]) => key));
  freshChildren.forEach((freshChild, index) => {
    const key = freshChild.dataset?.stateKey || freshChild.dataset?.turnId || "";
    const candidate = (key && keyed.get(key)) || oldChildren[index];
    if (candidate && !used.has(candidate) && candidate.nodeName === freshChild.nodeName) {
      used.add(candidate);
      const result = morphNode(candidate, freshChild);
      if (result !== candidate) candidate.replaceWith(result);
      if (candidate !== existing.childNodes[index]) existing.insertBefore(candidate, existing.childNodes[index] || null);
      return;
    }
    existing.insertBefore(freshChild, existing.childNodes[index] || null);
  });
  for (const child of [...existing.childNodes]) if (!used.has(child) && !freshChildren.includes(child)) child.remove();
  if (wasOpen !== null) existing.open = wasOpen;
  existing.scrollTop = scrollTop;
  return existing;
}

function scheduleTurnRender() {
  if (state.renderFrame) return;
  state.renderFrame = requestAnimationFrame(() => {
    state.renderFrame = 0;
    renderTurns();
  });
}

function turnNodes() {
  // children 只包含直接子节点，不受 :scope 选择器实现差异影响。
  return [...els.turnList.children].filter((node) => node.classList.contains("turn"));
}

function renderTurnId(turn) {
  return String(turn.local_id || turn.web_run_id || turn.run_id || "history");
}

function turnRenderSignature(turn) {
  // 只包含影响 DOM 的字段，避免事件数组增长导致无意义的重绘。
  return JSON.stringify({
    user_message: turn.user_message || "",
    reasoning_steps: turn.reasoning_steps || [],
    status: turn.status || "",
    pending_approval: Boolean(turn.pending_approval),
    pending_question: turn.pending_question || "",
    pending_choices: turn.pending_choices || [],
    final_text: turn.final_text || "",
    assistant_message: turn.assistant_message || "",
  });
}

function captureTurnScrollState() {
  if (state.followLatest) return { followBottom: true };
  const visibleTurn = turnNodes().find(
    (node) => node.offsetTop + node.offsetHeight > els.turnList.scrollTop,
  );
  if (!visibleTurn) return { followBottom: false };
  return {
    followBottom: false,
    turnId: visibleTurn.dataset.turnId,
    offset: visibleTurn.offsetTop - els.turnList.scrollTop,
  };
}

function restoreTurnScrollState(scrollState) {
  if (scrollState.followBottom || state.followLatest) {
    state.programmaticScroll = true;
    els.turnList.scrollTop = els.turnList.scrollHeight;
    requestAnimationFrame(() => { state.programmaticScroll = false; });
    return;
  }
  if (!scrollState.turnId) return;
  const anchor = turnNodes().find((node) => node.dataset.turnId === scrollState.turnId);
  if (anchor) els.turnList.scrollTop = anchor.offsetTop - scrollState.offset;
}

function captureDetailState() {
  const states = new Map();
  els.turnList.querySelectorAll("[data-state-key], [data-dynamic-scroll-key]").forEach((node) => {
    const key = node.dataset.stateKey || node.dataset.dynamicScrollKey;
    states.set(key, { open: node.open, scrollTop: node.scrollTop });
  });
  return states;
}

function restoreDetailState(states) {
  els.turnList.querySelectorAll("[data-state-key], [data-dynamic-scroll-key]").forEach((node) => {
    const key = node.dataset.stateKey || node.dataset.dynamicScrollKey;
    const saved = states.get(key);
    if (!saved) return;
    if ("open" in node) node.open = saved.open;
    node.scrollTop = saved.scrollTop;
  });
}

function renderTurn(turn) {
  const item = document.createElement("article");
  item.className = "turn";
  item.dataset.turnId = renderTurnId(turn);
  if (turn.user_message) item.append(messageNode("user", turn.user_message));
  const steps = turn.reasoning_steps || [];
  const compressionSummary = runCompressionSummary(steps);
  if (compressionSummary) {
    const summary = document.createElement("div");
    summary.className = "run-compression-summary";
    summary.textContent = compressionSummary;
    item.append(summary);
  }
  if (shouldShowStepTimeline(turn)) item.append(stepTimeline(turn));
  if (turn.pending_approval) item.append(approvalNode(turn));
  if (turn.final_text || turn.assistant_message) item.append(finalAnswerNode(turn.final_text || turn.assistant_message));
  return item;
}

function runCompressionSummary(steps) {
  const counts = { 1: 0, 2: 0, 3: 0, 4: 0 };
  let fallback = 0;
  for (const step of steps) {
    const comparison = step.compression_comparison;
    if (!comparison) continue;
    const level = Number(comparison.pressure_level || comparison.before?.pressure_level || 0);
    if (level < 1 || level > 4) continue;
    counts[level] += 1;
    if (comparison.result?.status === "fallback") fallback += 1;
  }
  const counted = Object.entries(counts).filter(([, count]) => count);
  const parts = counted.map(([level, count]) => `${level} 档 ${count} 次`);
  if (!parts.length) return "";
  const total = counted.reduce((sum, [, count]) => sum + count, 0);
  return `本次运行发生 ${total} 次压缩：${parts.join("、")}${fallback ? `（其中 ${fallback} 次降级为规则压缩）` : ""}。`;
}

function messageNode(role, content) {
  const node = document.createElement("section");
  node.className = `message ${role}`;
  node.innerHTML = `<span>${escapeHtml(role)}</span><p>${escapeHtml(content)}</p>`;
  return node;
}

function finalAnswerNode(content) {
  const node = document.createElement("details");
  node.className = "final-answer";
  node.open = true;
  node.innerHTML = `
    <summary class="final-head">
      <span class="eyebrow">最终答案</span>
      <span class="final-summary-actions">
        <span class="final-chip">完成</span>
        <span class="final-toggle" aria-hidden="true">⌄</span>
      </span>
    </summary>
    <pre>${escapeHtml(content)}</pre>
  `;
  return node;
}

function stepTimeline(turn) {
  const section = document.createElement("section");
  section.className = "step-timeline";
  const steps = turn.reasoning_steps || [];
  for (const step of steps) {
    section.append(stepItem(turn, step));
  }
  if (turn.status === "running") {
    const progress = document.createElement("div");
    progress.className = "step-progress";
    progress.textContent = "正在推理中";
    section.append(progress);
  }
  return section;
}

function shouldShowStepTimeline(turn) {
  return Boolean((turn.reasoning_steps || []).length);
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
        <div class="step-summary-text">${escapeHtml(summarizeText(step.process_content || "模型已返回工具调用", 80))}</div>
      </div>
    </summary>
  `;
  const body = document.createElement("div");
  body.className = "step-body";
  if (step.context_audit_ref) {
    body.append(contextAuditBlock(step.context_audit_ref, `step-context:${turnKey(turn)}:${step.step_id}`));
  }
  if (step.process_content) {
    body.append(detailBlock("模型过程消息", step.process_content, `step-content:${turnKey(turn)}:${step.step_id}`));
  }
  if (step.compression_before || step.compression_comparison) {
    body.append(compressionCard(turn, step));
  }
  if (step.error_text) {
    const error = document.createElement("section");
    error.className = "step-error-text";
    // 直接把后端返回的错误原文露出来，便于排根因。
    error.innerHTML = `
      <span class="eyebrow">错误信息</span>
      <pre>${escapeHtml(step.error_text)}</pre>
    `;
    body.append(error);
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
    // 隐藏模型请求和工具执行底层事件，只留下有业务价值的上下文与工具明细。
    if (!DISPLAYABLE_DETAIL_EVENTS.has(detail.event)) continue;
    if (HIDDEN_REASON_DETAIL_EVENTS.has(detail.event) || detail.event === "model_responded" || detail.event === "model_parsed") continue;
    if (detail.event === "context_built") {
      body.append(detailBlock("完整上下文文本", detail.content || "", `step-detail:${turnKey(turn)}:${step.step_id}:${detail.event}:${detail.created_at || ""}`));
      continue;
    }
    body.append(detailBlock(detail.title || detail.event, detail.content || "", `step-detail:${turnKey(turn)}:${step.step_id}:${detail.event}:${detail.created_at || ""}`));
  }
  details.append(body);
  return details;
}

function compressionCard(turn, step) {
  const comparison = step.compression_comparison || {};
  const before = comparison.before || step.compression_before || {};
  const level = Number(before.pressure_level || comparison.pressure_level || 0);
  if (level === 4) {
    return renderFourthLevelCompressionCard(turn, step, comparison, before);
  }
  return renderStandardCompressionCard(turn, step, comparison, before);
}

function renderStandardCompressionCard(turn, step, comparison, before) {
  const after = comparison.after;
  const result = comparison.result || {};
  const level = Number(before.pressure_level || comparison.pressure_level || 0);
  const status = result.label || (level === 0 ? "未压缩" : (result.status === "fallback" ? "降级为规则压缩" : "已压缩"));
  const details = document.createElement("details");
  details.className = "mini-detail compression-card";
  rememberOpenState(details, `compression:${turnKey(turn)}:${step.step_id}`, false);
  details.innerHTML = `<summary><span>上下文压缩：${escapeHtml(status)} · ${escapeHtml(String(level))} 档 · 压力 ${escapeHtml(formatRatio(before.pressure_ratio))} · ${escapeHtml(String(before.total_input_tokens || 0))} tokens</span></summary>`;
  const body = document.createElement("div");
  body.className = "compression-body";
  body.append(compressionMetricBlock("压缩前", before));
  if (after) {
    body.append(compressionMetricBlock("压缩后", after));
    const delta = comparison.delta || {};
    body.append(compressionDeltaBlock(delta));
    const changes = comparison.content_changes || [];
    body.append(detailBlock("内容差值", changes.length ? changes.map((item) => `${item.change || "变化"}｜第 ${item.turn || ""} 轮｜${item.tool_name || item.category || ""}\n${item.summary || item.file_ref || ""}`).join("\n\n") : "本次没有移出内容", `compression-content:${turnKey(turn)}:${step.step_id}`));
    body.append(detailBlock("压缩结果", JSON.stringify(result, null, 2), `compression-result:${turnKey(turn)}:${step.step_id}`));
  }
  details.append(body);
  return details;
}

function renderFourthLevelCompressionCard(turn, step, comparison, before) {
  const after = comparison.after || {};
  const delta = comparison.delta || {};
  const result = comparison.result || {};
  const fallback = result.status === "fallback";
  const status = fallback ? "降级为规则压缩" : "已压缩";
  const details = document.createElement("details");
  details.className = "mini-detail compression-card compression-card-fourth-level";
  // 第四档默认折叠，避免完整摘要在消息区抢占首屏空间。
  rememberOpenState(details, `compression:${turnKey(turn)}:${step.step_id}`, false);
  details.innerHTML = `<summary><span>上下文压缩：${escapeHtml(status)} · 4 档 · 压力 ${escapeHtml(formatRatio(before.pressure_ratio))} · ${escapeHtml(String(before.total_input_tokens || 0))} tokens</span></summary>`;

  const body = document.createElement("div");
  body.className = "compression-body";
  body.append(compressionMetricBlock("压缩前", before));
  body.append(compressionMetricBlock("压缩后", after));
  body.append(compressionDeltaBlock(delta));

  const summarySection = document.createElement("section");
  summarySection.className = "compression-summary-text";
  const summaryText = result.summary_text || "";
  summarySection.innerHTML = `<strong>压缩后摘要</strong><pre>${escapeHtml(summaryText || "压缩摘要为空")}</pre>`;
  body.append(summarySection);

  const auditSection = document.createElement("section");
  auditSection.className = "compression-audit-fields";
  const fields = [
    ["压缩状态", status],
    ["触发条件", result.trigger || ""],
    ["摘要来源", result.summary_source || ""],
    ["artifact 引用", result.artifact_ref || ""],
  ];
  auditSection.innerHTML = `<strong>压缩状态与审计信息</strong>${fields.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><span>${escapeHtml(String(value || "未提供"))}</span></div>`).join("")}`;
  body.append(auditSection);
  details.append(body);
  return details;
}

function formatRatio(value) {
  const ratio = Number(value || 0);
  return `${(ratio <= 1 ? ratio * 100 : ratio).toFixed(1)}%`;
}

function compressionMetricBlock(title, data) {
  const section = document.createElement("section");
  section.className = "compression-metrics";
  const fixed = Object.entries(data.fixed_items || {}).map(([name, tokens]) => `${name}: ${tokens}`).join("，");
  section.innerHTML = `<strong>${escapeHtml(title)}</strong><div>固定项：${escapeHtml(fixed || "无")}</div><div>总输入：${escapeHtml(String(data.total_input_tokens || 0))} tokens；输出预留：${escapeHtml(String(data.output_reserved_tokens || 0))}；安全余量：${escapeHtml(String(data.safety_margin_tokens || 0))}</div><div>剩余容量：${escapeHtml(String(data.remaining_capacity_tokens || 0))}；压力：${escapeHtml(formatRatio(data.pressure_ratio))}（${escapeHtml(String(data.pressure_level || 0))} 档）</div>`;
  return section;
}

function compressionDeltaBlock(delta) {
  const section = document.createElement("section");
  section.className = "compression-delta";
  const fixed = Object.entries(delta.fixed_items || {}).map(([name, value]) => `${name}: ${value > 0 ? "+" : ""}${value}`).join("，");
  section.innerHTML = `<strong>Token 差值</strong><div>${escapeHtml(fixed || "无")}</div><div>总释放：${escapeHtml(String(delta.total_released_tokens || 0))} tokens</div>`;
  return section;
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
  if (tool.artifact_ref) {
    body.append(toolArtifactNode(tool.artifact_ref));
  }
  details.append(body);
  return details;
}

function toolArtifactNode(ref) {
  const section = document.createElement("section");
  section.className = "tool-artifact";
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "查看完整输出";
  const path = document.createElement("code");
  path.textContent = ref;
  section.append(button, path);
  button.addEventListener("click", async () => {
    if (!state.projectId || button.dataset.loaded === "true") return;
    button.disabled = true;
    button.textContent = "加载中";
    try {
      const data = await api(`/api/projects/${encodeURIComponent(state.projectId)}/tool-artifact?ref=${encodeURIComponent(ref)}`);
      const loadedDetail = detailBlock("完整输出", data.content || "", `tool-artifact:${state.projectId}:${ref}`);
      loadedDetail.dataset.contentLoaded = "true";
      loadedDetail.dataset.loaded = "true";
      loadedDetail.querySelector("pre")?.setAttribute("data-content-loaded", "true");
      loadedDetail.querySelector("pre")?.setAttribute("data-dynamic-scroll-key", `${loadedDetail.dataset.stateKey}:content`);
      section.append(loadedDetail);
      button.dataset.loaded = "true";
      button.textContent = "完整输出已加载";
    } catch (error) {
      button.disabled = false;
      button.textContent = "无法加载完整输出";
      path.textContent = error.message;
    }
  });
  return section;
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

function contextAuditBlock(ref, key) {
  const details = document.createElement("details");
  details.className = "mini-detail";
  rememberOpenState(details, key, false);
  const summary = document.createElement("summary");
  summary.textContent = "完整上下文审计";
  const pre = document.createElement("pre");
  pre.dataset.dynamicScrollKey = `${key}:content`;
  pre.textContent = ref;
  details.append(summary, pre);
  details.addEventListener("toggle", async () => {
    if (!details.open || details.dataset.loaded === "true") return;
    try {
      const data = await api(`/api/projects/${encodeURIComponent(state.projectId)}/context-audit?ref=${encodeURIComponent(ref)}`);
      pre.textContent = JSON.stringify(data.context_result || data, null, 2);
      details.dataset.loaded = "true";
      details.dataset.contentLoaded = "true";
      details.dataset.loaded = "true";
      pre.dataset.contentLoaded = "true";
    } catch (error) {
      pre.textContent = `无法读取审计文件: ${error.message}`;
    }
  });
  return details;
}

function rememberOpenState(details, key, defaultOpen = false) {
  details.dataset.stateKey = key;
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
    const runId = turn.web_run_id;
    if (!answer || !runId || !state.projectId || !state.sessionId) return;
    await api(`/api/runs/${encodeURIComponent(runId)}/approval?project_id=${encodeURIComponent(state.projectId)}&session_id=${encodeURIComponent(state.sessionId)}`, {
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
  return labels[status] || status || "";
}

function toolStatusLabel(status) {
  const labels = {
    pending: "待执行",
    running: "执行中",
    success: "成功",
    error: "失败",
    timeout: "超时",
  };
  return labels[status] || status || "";
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
  // 历史加载与实时 step_patch 都使用同一排序，切换会话不会改变步骤位置。
  turn.reasoning_steps = [...turn.stepMap.values()].sort(compareSteps);
  return turn;
}

function sortTurns() {
  state.turns.sort(compareTurns);
}

function compareTurns(a, b) {
  const as = Number(a.sequence);
  const bs = Number(b.sequence);
  const aHasSequence = Number.isFinite(as);
  const bHasSequence = Number.isFinite(bs);
  if (aHasSequence && bHasSequence && as !== bs) return as - bs;
  if (aHasSequence !== bHasSequence) return aHasSequence ? -1 : 1;
  const at = String(a.created_at || "");
  const bt = String(b.created_at || "");
  if (at !== bt) return at < bt ? -1 : 1;
  return String(a.local_id || a.web_run_id || a.run_id || "").localeCompare(String(b.local_id || b.web_run_id || b.run_id || ""));
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
  const streamProjectId = state.projectId;
  const streamSessionId = state.sessionId;
  const streamEpoch = state.selectionEpoch;
  const activeTurn = state.turns.find((turn) => turn.web_run_id === runId || turn.run_id === runId);
  const after = Number(activeTurn?.event_cursor || 0);
  state.activeRunId = runId;
  const source = new EventSource(`/api/projects/${encodeURIComponent(state.projectId)}/runs/${encodeURIComponent(runId)}/events?session_id=${encodeURIComponent(streamSessionId)}&after=${encodeURIComponent(after)}`);
  state.eventSource = source;
  for (const name of STREAM_EVENTS) {
    source.addEventListener(name, (event) => handleRunEvent(name, event, { streamProjectId, streamSessionId, runId, streamEpoch, source }));
  }
  source.onerror = () => {
    if (state.selectionEpoch !== streamEpoch || state.eventSource !== source || state.sessionId !== streamSessionId) return;
    source.close();
    state.eventSource = null;
    setRunStatus("disconnected");
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(() => {
      if (state.selectionEpoch === streamEpoch && state.sessionId === streamSessionId && state.activeRunId === runId) connectEvents(runId);
    }, 1000);
  };
}

function stopStream() {
  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = null;
  if (state.renderFrame) cancelAnimationFrame(state.renderFrame);
  state.renderFrame = 0;
  if (state.eventSource) state.eventSource.close();
  state.eventSource = null;
  state.eventIds.clear();
}

function handleRunEvent(name, event, stream = {}) {
  const payload = JSON.parse(event.data);
  // EventSource 关闭存在尾部回调窗口，按创建流时的会话和 run 再做一次隔离。
  if (stream.streamProjectId && state.projectId !== stream.streamProjectId) return;
  if (stream.streamSessionId && state.sessionId !== stream.streamSessionId) return;
  if (stream.streamEpoch !== undefined && state.selectionEpoch !== stream.streamEpoch) return;
  if (stream.source && state.eventSource !== stream.source) return;
  if (payload.project_id && stream.streamProjectId && payload.project_id !== stream.streamProjectId) return;
  if (payload.session_id && stream.streamSessionId && payload.session_id !== stream.streamSessionId) return;
  const payloadRunIds = [payload.web_run_id, payload.jcode_run_id, payload.run_id].filter(Boolean).map(String);
  if (stream.runId && payloadRunIds.length && !payloadRunIds.includes(String(stream.runId)) && !payloadRunIds.includes(String(state.activeRunId))) return;
  const eventId = payload.event_id || event.lastEventId || `${name}:${Date.now()}`;
  if (state.eventIds.has(eventId)) return;
  state.eventIds.add(eventId);
  if (!state.followLatest && name !== "stream_closed") state.newContentPending = true;

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
    turn.event_cursor = Math.max(Number(turn.event_cursor || 0), Number(payload.event_cursor || 0));
    scheduleTurnRender();
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
  } else if (name === "runtime_stopped") {
    turn.status = "stopped";
    setRunStatus("stopped");
  } else if (name === "web_run_completed" || name === "run_finished") {
    turn.status = name === "run_finished" && payload.status !== "completed" ? "stopped" : "completed";
    setRunStatus(turn.status);
    if (name === "web_run_completed" && payload.final_text) {
      turn.final_text = payload.final_text;
      turn.assistant_message = payload.final_text;
    }
    loadSessions(false).catch(console.error);
  } else if (name !== "stream_closed") {
    // aborting 期间允许继续展示工具收尾事件，但不能恢复为 running。
    if (turn.status !== "waiting_approval" && turn.status !== "aborting") turn.status = "running";
    if (turn.status === "running") setRunStatus("running");
  }
  if (name === "approval_answered") {
    turn.pending_approval = false;
    turn.pending_question = "";
    turn.pending_choices = [];
  }
  if (name !== "stream_closed") {
    turn.events.push(payload);
    turn.event_cursor = Math.max(Number(turn.event_cursor || 0), Number(payload.event_cursor || 0));
  } else {
    stopStream();
  }
  scheduleTurnRender();
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
    reasoning_steps: [],
    final_text: "",
    assistant_message: "",
    status: "running",
    events: [],
    pending_approval: false,
    stepMap: new Map(),
  });
  state.turns.push(turn);
  sortTurns();
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
      reasoning_steps: [],
      final_text: "",
      assistant_message: "",
      status: "running",
      created_at: new Date().toISOString(),
      sequence: state.turns.length,
      events: [],
      pending_approval: false,
      stepMap: new Map(),
    }),
  );
  els.messageInput.value = "";
  setRunStatus("running");
  renderTurns();
  const originProjectId = state.projectId;
  const originSessionId = state.sessionId;
  const originEpoch = state.selectionEpoch;
  try {
    const run = await api(`/api/projects/${encodeURIComponent(originProjectId)}/sessions/${encodeURIComponent(originSessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    if (originEpoch !== state.selectionEpoch || originProjectId !== state.projectId || originSessionId !== state.sessionId) return;
    const turn = activeTurn({ web_run_id: run.web_run_id });
    turn.web_run_id = run.web_run_id;
    turn.run_id = run.jcode_run_id || run.run_id || "";
    state.activeRunId = run.web_run_id;
    connectEvents(run.web_run_id);
  } catch (error) {
    if (originEpoch !== state.selectionEpoch || originProjectId !== state.projectId || originSessionId !== state.sessionId) return;
    const turn = activeTurn();
    turn.status = "failed";
    turn.events.push({ event: "client_error", created_at: new Date().toISOString(), error_type: "request_failed", result: error.message });
    setRunStatus("error");
    renderTurns();
  }
});

els.stopRun.addEventListener("click", async () => {
  const turn = state.turns.find((item) => item.pending_approval || ["running", "waiting_approval", "aborting"].includes(item.status));
  const runId = turn?.web_run_id || state.activeRunId;
  if (!runId || !state.projectId || !state.sessionId) return;
  // 后端会立即进入 aborting；本地先显示，避免等待 HTTP/SSE 延迟时界面无反馈。
  setRunStatus("aborting");
  if (turn) turn.status = "aborting";
  renderTurns();
  try {
    const run = await api(`/api/runs/${encodeURIComponent(runId)}/abort?project_id=${encodeURIComponent(state.projectId)}&session_id=${encodeURIComponent(state.sessionId)}`, { method: "POST" });
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

els.modelProfile.addEventListener("change", async () => {
  if (!state.projectId || !state.sessionId) return;
  updateReasoningControls(state.modelProfiles, els.modelProfile.value);
  const session = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions/${encodeURIComponent(state.sessionId)}/model`, {
    method: "POST",
    body: JSON.stringify({ model_profile: els.modelProfile.value, thinking_enabled: els.thinkingEnabled.checked, reasoning_effort: els.reasoningEffort.value }),
  });
  state.modelProfiles = session.model_profiles || [];
  renderModelProfiles(state.modelProfiles, session.active_model_profile || "");
  await loadSessions(false);
});

async function saveReasoningOptions() {
  if (!state.projectId || !state.sessionId || els.reasoningEffort.dataset.supported !== "true") return;
  const session = await api(`/api/projects/${encodeURIComponent(state.projectId)}/sessions/${encodeURIComponent(state.sessionId)}/model`, {
    method: "POST",
    body: JSON.stringify({ model_profile: els.modelProfile.value, thinking_enabled: els.thinkingEnabled.checked, reasoning_effort: els.reasoningEffort.value }),
  });
  state.modelProfiles = session.model_profiles || [];
  renderModelProfiles(state.modelProfiles, session.active_model_profile || "");
}

els.reasoningEffort.addEventListener("change", saveReasoningOptions);
els.thinkingEnabled.addEventListener("change", saveReasoningOptions);

loadProjects(true).catch((error) => {
  state.turns = [
    normalizeTurn({
      local_id: "startup-error",
      status: "failed",
      user_message: "",
      reasoning_steps: [],
      final_text: "",
      assistant_message: "",
      events: [{ event: "client_error", created_at: new Date().toISOString(), error_type: "startup_failed", result: error.message }],
      stepMap: new Map(),
    }),
  ];
  renderTurns();
});
