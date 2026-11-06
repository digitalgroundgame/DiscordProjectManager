/**
 * DGG-PM Read-Only Web Dashboard & Portal Controller
 * Connects to /api/me, /api/projects, /api/projects/{id}/workspace, and /api/projects/{id}/tech-tree
 */

const STATE_COLORS = {
  complete: "#23a55a",
  active: "#f0b232",
  available: "#5865f2",
  locked: "#4e5058",
  blocked: "#f23f43",
};

const STATE_BADGES = {
  complete: "COMPLETE",
  active: "ACTIVE",
  available: "AVAILABLE",
  locked: "LOCKED",
  blocked: "BLOCKED",
};

const STATUS_LABELS = {
  notStarted: "Not Started",
  inProgress: "In Progress",
  completed: "Completed",
};

const PRIORITY_LABELS = {
  high: "High",
  normal: "Normal",
  low: "Low",
};

const state = {
  user: null,
  projects: [],
  activeProjectId: null,
  workspace: null,
  techTree: null,
  activeView: "tech-tree",
  dagOrientation: "LR",
  highlightCriticalPath: false,
  discordLinkMode: localStorage.getItem("dgg_pm_discord_link_mode") === "web" ? "web" : "app",
  selectedTaskId: null,
  cy: null,
};

const el = (id) => document.getElementById(id);

async function fetchJson(url) {
  const resp = await fetch(url, { credentials: "same-origin" });
  if (!resp.ok) {
    const err = new Error(`HTTP ${resp.status}`);
    err.status = resp.status;
    try {
      err.body = await resp.json();
    } catch {
      err.body = {};
    }
    throw err;
  }
  return resp.json();
}

const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

function sanitizeDiscordHref(url) {
  if (!url || typeof url !== "string") return null;
  const trimmed = url.trim();
  if (/^(?:https?:\/\/|discord:\/\/)/i.test(trimmed)) {
    return trimmed;
  }
  return null;
}

function formatDate(isoStr) {
  if (!isoStr) return "—";
  const dt = new Date(isoStr);
  if (Number.isNaN(dt.getTime())) return isoStr;
  return dt.toISOString().slice(0, 10);
}

function resolveDiscordAppUrl(item) {
  if (!item) return null;
  if (item.discord_app_url) return sanitizeDiscordHref(item.discord_app_url);
  if (item.discord_jump_url) {
    return sanitizeDiscordHref(
      item.discord_jump_url.replace(/^https?:\/\/(?:ptb\.|canary\.)?discord\.com\/channels\//, "discord://-/channels/")
    );
  }
  return null;
}

function resolveDiscordLink(item) {
  if (!item) return { url: null, isWeb: state.discordLinkMode === "web" };
  if (state.discordLinkMode === "web") {
    return { url: sanitizeDiscordHref(item.discord_jump_url), isWeb: true };
  }
  return { url: resolveDiscordAppUrl(item), isWeb: false };
}

function syncLinkModeToggleUI() {
  const isWeb = state.discordLinkMode === "web";
  el("link-mode-app")?.classList.toggle("active", !isWeb);
  el("link-mode-web")?.classList.toggle("active", isWeb);
}

function setDiscordLinkMode(mode) {
  state.discordLinkMode = mode === "web" ? "web" : "app";
  localStorage.setItem("dgg_pm_discord_link_mode", state.discordLinkMode);
  syncLinkModeToggleUI();
  updateProjectHeaderAndMetrics();
  renderCurrentView();
  if (state.selectedTaskId && !el("task-inspector").classList.contains("hidden")) {
    openInspector(state.selectedTaskId);
  }
}

function showGate(title, message, showLogin = true) {
  el("portal-main").classList.add("hidden");
  el("project-controls").classList.add("hidden");
  el("auth-gate").classList.remove("hidden");
  el("gate-title").textContent = title;
  el("gate-message").textContent = message;
  el("login-btn").classList.toggle("hidden", !showLogin);
}

async function initPortal() {
  bindEvents();
  try {
    state.user = await fetchJson("/api/me");
  } catch (err) {
    if (err.status === 403) {
      showGate(
        "Server Membership Required",
        err.body?.detail || "You must be a member of the configured Discord server to view workspaces.",
        false
      );
      el("logout-btn").classList.remove("hidden");
      return;
    }
    showGate(
      "Sign in to view Project Workspaces",
      "Authenticate with Discord OAuth2 to explore project roadmaps, Kanban boards, and interactive Tech Tree DAGs.",
      true
    );
    return;
  }

  // Authenticated header state
  el("login-btn").classList.add("hidden");
  el("logout-btn").classList.remove("hidden");
  const userBadge = el("user-badge");
  const roleTag = state.user.is_guild_admin ? "Server Manager" : "Member";
  userBadge.textContent = `${state.user.display_name} (${roleTag})`;
  userBadge.classList.remove("hidden");

  await loadProjects();
}

async function loadProjects() {
  const data = await fetchJson("/api/projects");
  state.projects = data.projects || [];

  if (state.projects.length === 0) {
    showGate(
      "No Accessible Projects",
      "You do not currently have permission to view any active projects in this server.",
      false
    );
    return;
  }

  el("auth-gate").classList.add("hidden");
  el("portal-main").classList.remove("hidden");
  el("project-controls").classList.remove("hidden");

  const select = el("project-select");
  select.innerHTML = "";
  for (const proj of state.projects) {
    const opt = document.createElement("option");
    opt.value = proj.id;
    opt.textContent = `[${proj.prefix}] ${proj.name}`;
    select.appendChild(opt);
  }

  state.activeProjectId = state.projects[0].id;
  select.value = state.activeProjectId;
  await loadActiveProjectData();
}

async function loadActiveProjectData() {
  if (!state.activeProjectId) return;
  const [ws, tt] = await Promise.all([
    fetchJson(`/api/projects/${state.activeProjectId}/workspace`),
    fetchJson(`/api/projects/${state.activeProjectId}/tech-tree`),
  ]);
  state.workspace = ws;
  state.techTree = tt;

  updateProjectHeaderAndMetrics();
  populateFilterDropdowns();
  renderCurrentView();
}

function updateProjectHeaderAndMetrics() {
  const proj = state.workspace?.project;
  const ttMetrics = state.techTree?.metrics;
  const projMetrics = proj?.metrics;

  const jumpLink = el("project-discord-link");
  const { url: projUrl, isWeb } = resolveDiscordLink(proj);
  if (projUrl) {
    jumpLink.href = projUrl;
    if (isWeb) {
      jumpLink.setAttribute("target", "_blank");
      jumpLink.setAttribute("rel", "noopener noreferrer");
    } else {
      jumpLink.removeAttribute("target");
      jumpLink.removeAttribute("rel");
    }
    jumpLink.textContent = isWeb ? "Open Project in Web ↗" : "Open Project in App ↗";
    jumpLink.classList.remove("hidden");
  } else {
    jumpLink.classList.add("hidden");
  }

  el("metric-completion").textContent = `${ttMetrics?.percent_complete ?? projMetrics?.percent_complete ?? 0}%`;
  el("metric-total").textContent = String(ttMetrics?.total_tasks ?? projMetrics?.total_tasks ?? 0);
  el("metric-complete").textContent = String(ttMetrics?.completed_tasks ?? projMetrics?.completed_tasks ?? 0);
  el("metric-active").textContent = String(ttMetrics?.active_tasks ?? projMetrics?.in_progress_tasks ?? 0);
  el("metric-available").textContent = String(ttMetrics?.available_tasks ?? 0);
  el("metric-locked").textContent = String(ttMetrics?.locked_tasks ?? 0);
  el("metric-overdue").textContent = String(projMetrics?.overdue_tasks ?? 0);
}

function populateFilterDropdowns() {
  const squadSelect = el("filter-squad");
  const currentSquad = squadSelect.value;
  squadSelect.innerHTML = '<option value="">All Squads</option>';
  for (const sq of state.workspace?.squads || []) {
    const opt = document.createElement("option");
    opt.value = sq.id;
    opt.textContent = sq.name;
    squadSelect.appendChild(opt);
  }
  squadSelect.value = currentSquad;

  const tagSelect = el("filter-tag");
  const currentTag = tagSelect.value;
  tagSelect.innerHTML = '<option value="">All Tags</option>';
  for (const tag of state.workspace?.filters?.tags || []) {
    const opt = document.createElement("option");
    opt.value = tag;
    opt.textContent = tag;
    tagSelect.appendChild(opt);
  }
  tagSelect.value = currentTag;
}

function getFilteredTasks() {
  const tasks = state.workspace?.tasks || [];
  const search = (el("filter-search").value || "").trim().toLowerCase();
  const squadId = el("filter-squad").value;
  const tag = el("filter-tag").value;
  const dueMode = el("filter-due").value;
  const overdueOnly = el("filter-overdue-only").checked;
  const now = new Date();
  const in7Days = new Date(now.getTime() + 7 * 86400 * 1000);

  return tasks.filter((t) => {
    if (search) {
      const hay = `${t.short_id} ${t.title} ${t.body || ""}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (overdueOnly && !t.is_overdue) return false;
    if (squadId) {
      const hasSquad = (t.squads || []).some((s) => s.id === squadId);
      if (!hasSquad) return false;
    }
    if (tag) {
      if (!(t.tags || []).includes(tag)) return false;
    }
    if (dueMode === "overdue" && !t.is_overdue) return false;
    if (dueMode === "has_due" && !t.due_at) return false;
    if (dueMode === "no_due" && t.due_at) return false;
    if (dueMode === "due_soon") {
      if (!t.due_at || t.is_completed) return false;
      const dt = new Date(t.due_at);
      if (dt < now || dt > in7Days) return false;
    }
    return true;
  });
}

function renderCurrentView() {
  el("view-tech-tree").classList.toggle("hidden", state.activeView !== "tech-tree");
  el("view-kanban").classList.toggle("hidden", state.activeView !== "kanban");
  el("view-matrix").classList.toggle("hidden", state.activeView !== "matrix");

  if (state.activeView === "tech-tree") {
    renderTechTree();
  } else if (state.activeView === "kanban") {
    renderKanban();
  } else {
    renderMatrix();
  }
}

function escapeXml(unsafe) {
  if (unsafe == null) return "";
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function wrapNodeTitle(text, maxChars = 36, maxLines = 2) {
  const words = text.split(/\s+/);
  const lines = [];
  let current = "";
  for (const w of words) {
    if (lines.length === maxLines - 1) {
      const candidate = current ? `${current} ${w}` : w;
      if (candidate.length <= maxChars) {
        current = candidate;
      } else {
        current = current ? `${current}…` : `${w.slice(0, maxChars - 1)}…`;
        break;
      }
    } else {
      const candidate = current ? `${current} ${w}` : w;
      if (candidate.length <= maxChars) {
        current = candidate;
      } else {
        lines.push(current);
        current = w;
      }
    }
  }
  if (current) lines.push(current);
  return lines;
}

function createNodeSvg({
  shortId,
  title,
  state,
  isOverdue,
  isOnCriticalPath,
  width = 260,
  height = 74,
}) {
  const stateColor = isOverdue
    ? "#f23f43"
    : STATE_COLORS[state] || STATE_COLORS.locked;
  const isLocked = state === "locked";

  const badgeText = isOverdue
    ? "OVERDUE"
    : STATE_BADGES[state] || state.toUpperCase();

  const shortIdColor = isLocked ? "#949ba4" : stateColor;
  const badgeTextColor = isLocked ? "#949ba4" : stateColor;

  const badgeBg = isOverdue
    ? "rgba(242, 63, 67, 0.15)"
    : state === "active"
      ? "rgba(240, 178, 50, 0.15)"
      : state === "complete"
        ? "rgba(35, 165, 90, 0.15)"
        : state === "available"
          ? "rgba(88, 101, 242, 0.15)"
          : "rgba(255, 255, 255, 0.05)";

  const badgeWidth = Math.max(56, badgeText.length * 6.5 + 16);
  const badgeX = width - badgeWidth - 12;

  const outerBorder = isOnCriticalPath
    ? "#ffffff"
    : "rgba(255, 255, 255, 0.1)";
  const outerBorderWidth = isOnCriticalPath ? 2.5 : 1;

  const titleColor = isLocked ? "#949ba4" : "#f2f3f5";
  const lines = wrapNodeTitle(title, 36, 2);
  const titleSvg =
    lines.length === 1
      ? `<text x="14" y="52" fill="${titleColor}" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="12" font-weight="600">${escapeXml(lines[0])}</text>`
      : `<text x="14" y="44" fill="${titleColor}" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="11.5" font-weight="600">${escapeXml(lines[0])}</text>
         <text x="14" y="60" fill="${titleColor}" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="11.5" font-weight="600">${escapeXml(lines[1])}</text>`;

  const cardBorderRadius = 8;
  const barThickness = 5;
  const outerR = cardBorderRadius - 0.5;
  const innerR = outerR - barThickness;
  const topInnerY = 0.5 + barThickness;
  const botInnerY = height - 0.5 - barThickness;

  const leftBarPath = `M ${cardBorderRadius} 0.5 A ${outerR} ${outerR} 0 0 0 0.5 ${cardBorderRadius} L 0.5 ${height - cardBorderRadius} A ${outerR} ${outerR} 0 0 0 ${cardBorderRadius} ${height - 0.5} L ${cardBorderRadius} ${botInnerY} A ${innerR} ${innerR} 0 0 1 ${0.5 + barThickness} ${height - cardBorderRadius} L ${0.5 + barThickness} ${cardBorderRadius} A ${innerR} ${innerR} 0 0 1 ${cardBorderRadius} ${topInnerY} Z`;

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
    <rect x="0.5" y="0.5" width="${width - 1}" height="${height - 1}" rx="${cardBorderRadius}" fill="#2b2d31"/>
    <path d="${leftBarPath}" fill="${stateColor}"/>
    <rect x="0.5" y="0.5" width="${width - 1}" height="${height - 1}" rx="${cardBorderRadius}" fill="none" stroke="${outerBorder}" stroke-width="${outerBorderWidth}"/>
    <text x="14" y="24" fill="${shortIdColor}" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="12" font-weight="700">[${escapeXml(shortId)}]</text>
    <rect x="${badgeX}" y="10" width="${badgeWidth}" height="19" rx="9.5" fill="${badgeBg}"/>
    <text x="${badgeX + badgeWidth / 2}" y="23" fill="${badgeTextColor}" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="9.5" font-weight="700" text-anchor="middle">${escapeXml(badgeText)}</text>
    ${titleSvg}
  </svg>`;

  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

function renderTechTree() {
  const tt = state.techTree;
  if (!tt) return;

  const container = el("cy-canvas");
  const filteredIds = new Set(getFilteredTasks().map((t) => t.id));

  if (typeof window.cytoscape === "undefined") {
    // Fallback list rendering if CDN is offline
    container.innerHTML = "";
    const fallback = document.createElement("div");
    fallback.style.padding = "1rem";
    for (const node of tt.nodes) {
      const row = document.createElement("div");
      row.className = "dep-item";
      row.style.marginBottom = "0.5rem";
      const badge = STATE_BADGES[node.state] || node.state.toUpperCase();
      row.textContent = `[${node.short_id}] ${node.title} (${badge})`;
      row.addEventListener("click", () => openInspector(node.id));
      fallback.appendChild(row);
    }
    container.appendChild(fallback);
    return;
  }

  const elements = [];
  for (const n of tt.nodes) {
    const bgImage = createNodeSvg({
      shortId: n.short_id,
      title: n.title,
      state: n.state,
      isOverdue: Boolean(n.is_overdue),
      isOnCriticalPath: Boolean(state.highlightCriticalPath && n.is_on_critical_path),
      width: 260,
      height: 74,
    });

    elements.push({
      data: {
        id: n.id,
        short_id: n.short_id,
        state: n.state,
        bgImage,
        opacity: filteredIds.has(n.id) ? 1.0 : 0.3,
      },
    });
  }
  for (const e of tt.edges) {
    elements.push({
      data: {
        id: e.id,
        source: e.source,
        target: e.target,
        lineColor: state.highlightCriticalPath && e.is_critical_edge ? "#f0b232" : "#6d6f78",
        width: state.highlightCriticalPath && e.is_critical_edge ? 3.5 : 2,
      },
    });
  }

  if (state.cy) {
    state.cy.destroy();
  }

  state.cy = window.cytoscape({
    container,
    elements,
    style: [
      {
        selector: "node",
        style: {
          shape: "round-rectangle",
          width: 260,
          height: 74,
          "background-color": "#2b2d31",
          "background-image": "data(bgImage)",
          "background-fit": "cover",
          "background-clip": "none",
          "border-width": 0,
          opacity: "data(opacity)",
          label: "",
        },
      },
      {
        selector: "edge",
        style: {
          width: "data(width)",
          "line-color": "data(lineColor)",
          "target-arrow-color": "data(lineColor)",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
        },
      },
    ],
    layout: {
      name: "dagre",
      rankDir: state.dagOrientation,
      nodeSep: 50,
      rankSep: 85,
      padding: 30,
    },
  });

  state.cy.on("tap", "node", (evt) => {
    openInspector(evt.target.id());
  });
}

function renderKanban() {
  const tasks = getFilteredTasks();
  const groupBy = el("filter-group-by").value;
  const board = el("kanban-columns");
  board.innerHTML = "";

  const groups = new Map();
  if (groupBy === "status") {
    groups.set("notStarted", { title: "Not Started", items: [] });
    groups.set("inProgress", { title: "In Progress", items: [] });
    groups.set("completed", { title: "Completed", items: [] });
    for (const t of tasks) {
      (groups.get(t.status) || groups.get("notStarted")).items.push(t);
    }
  } else if (groupBy === "priority") {
    groups.set("high", { title: "High Priority", items: [] });
    groups.set("normal", { title: "Normal Priority", items: [] });
    groups.set("low", { title: "Low Priority", items: [] });
    for (const t of tasks) {
      (groups.get(t.priority) || groups.get("normal")).items.push(t);
    }
  } else {
    for (const t of tasks) {
      const key = t.assignee_name || "Unassigned";
      if (!groups.has(key)) groups.set(key, { title: key, items: [] });
      groups.get(key).items.push(t);
    }
  }

  for (const [, col] of groups) {
    const colEl = document.createElement("div");
    colEl.className = "kanban-col";
    colEl.innerHTML = `
      <div class="kanban-col-header">
        <span>${escapeHtml(col.title)}</span>
        <span class="task-short-id">${col.items.length}</span>
      </div>
      <div class="kanban-col-body"></div>
    `;
    const bodyEl = colEl.querySelector(".kanban-col-body");
    for (const t of col.items) {
      const card = document.createElement("div");
      card.className = "task-card";
      const overdueBadge = t.is_overdue ? '<span class="legend-pill state-overdue">Overdue</span>' : "";
      const { url: linkUrl, isWeb } = resolveDiscordLink(t);
      const targetAttr = isWeb ? ' target="_blank" rel="noopener noreferrer"' : "";
      const discordLink = linkUrl
        ? `<a href="${escapeHtml(linkUrl)}"${targetAttr} class="btn btn-sm btn-discord">${isWeb ? "Web" : "Discord"} &#8599;</a>`
        : "";
      const safeState = escapeHtml(t.readiness_state);
      card.innerHTML = `
        <div class="task-card-top">
          <span class="task-short-id">[${escapeHtml(t.short_id)}]</span>
          <div>
            ${overdueBadge}
            <span class="legend-pill state-${safeState}">${safeState}</span>
          </div>
        </div>
        <div class="task-card-title">${escapeHtml(t.title)}</div>
        <div class="task-card-footer">
          <span>${escapeHtml(t.assignee_name || "Unassigned")} • ${escapeHtml(formatDate(t.due_at))}</span>
          ${discordLink}
        </div>
      `;
      card.querySelector(".btn-discord")?.addEventListener("click", (e) => e.stopPropagation());
      card.addEventListener("click", () => openInspector(t.id));
      bodyEl.appendChild(card);
    }
    board.appendChild(colEl);
  }
}

function renderMatrix() {
  const tasks = getFilteredTasks();
  const tbody = el("matrix-tbody");
  tbody.innerHTML = "";

  for (const t of tasks) {
    const tr = document.createElement("tr");
    const squadNames = (t.squads || []).map((s) => s.name).join(", ");
    const tagNames = (t.tags || []).join(", ");
    const combinedMeta = [squadNames, tagNames].filter(Boolean).join(" | ") || "—";
    const dueLabel = t.is_overdue ? `${formatDate(t.due_at)} (Overdue)` : formatDate(t.due_at);
    const { url: linkUrl, isWeb } = resolveDiscordLink(t);
    const targetAttr = isWeb ? ' target="_blank" rel="noopener noreferrer"' : "";
    const jumpBtn = linkUrl
      ? `<a href="${escapeHtml(linkUrl)}"${targetAttr} class="btn btn-sm btn-discord">Open &#8599;</a>`
      : "—";
    const safeState = escapeHtml(t.readiness_state);

    tr.innerHTML = `
      <td class="task-short-id">[${escapeHtml(t.short_id)}]</td>
      <td><strong>${escapeHtml(t.title)}</strong></td>
      <td><span class="legend-pill state-${safeState}">${safeState}</span></td>
      <td>${escapeHtml(STATUS_LABELS[t.status] || t.status)}</td>
      <td>${escapeHtml(PRIORITY_LABELS[t.priority] || t.priority)}</td>
      <td>${escapeHtml(t.assignee_name || "Unassigned")}</td>
      <td>${escapeHtml(combinedMeta)}</td>
      <td>${escapeHtml(dueLabel)}</td>
      <td>${jumpBtn}</td>
    `;
    tr.querySelector(".btn-discord")?.addEventListener("click", (e) => e.stopPropagation());
    tr.addEventListener("click", () => openInspector(t.id));
    tbody.appendChild(tr);
  }
}

function openInspector(taskId) {
  const task =
    (state.workspace?.tasks || []).find((t) => t.id === taskId) ||
    (state.techTree?.nodes || []).find((n) => n.id === taskId);
  if (!task) return;

  state.selectedTaskId = task.id;
  const inspector = el("task-inspector");
  inspector.classList.remove("hidden");

  const readiness = task.readiness_state || task.state || "available";
  el("inspector-short-id").textContent = `[${task.short_id}]`;
  const rPill = el("inspector-readiness");
  rPill.textContent = readiness;
  rPill.className = `legend-pill state-${readiness}`;

  el("inspector-title").textContent = task.title;
  el("inspector-status").textContent = STATUS_LABELS[task.status] || task.status;
  el("inspector-priority").textContent = PRIORITY_LABELS[task.priority] || task.priority;
  el("inspector-assignee").textContent = task.assignee_name || "Unassigned";
  el("inspector-due").textContent = task.is_overdue
    ? `${formatDate(task.due_at)} (OVERDUE)`
    : formatDate(task.due_at);
  el("inspector-body").textContent = task.body || "No description provided.";

  const discordBtn = el("inspector-discord-btn");
  const { url: taskUrl, isWeb } = resolveDiscordLink(task);
  if (taskUrl) {
    discordBtn.href = taskUrl;
    if (isWeb) {
      discordBtn.setAttribute("target", "_blank");
      discordBtn.setAttribute("rel", "noopener noreferrer");
    } else {
      discordBtn.removeAttribute("target");
      discordBtn.removeAttribute("rel");
    }
    const targetName = isWeb ? "Web Browser" : "Discord App";
    discordBtn.textContent = task.has_dedicated_thread
      ? `Open Task Card in ${targetName} ↗`
      : `Open Project Forum in ${targetName} ↗`;
    discordBtn.classList.remove("hidden");
  } else {
    discordBtn.classList.add("hidden");
  }

  const renderDepList = (listEl, countEl, items) => {
    countEl.textContent = String(items.length);
    listEl.innerHTML = "";
    if (items.length === 0) {
      const empty = document.createElement("li");
      empty.style.color = "var(--text-muted)";
      empty.style.fontSize = "0.8rem";
      empty.textContent = "None";
      listEl.appendChild(empty);
      return;
    }
    for (const item of items) {
      const li = document.createElement("li");
      li.className = "dep-item";
      const safeItemState = escapeHtml(item.state);
      li.innerHTML = `
        <span><strong>[${escapeHtml(item.short_id)}]</strong> ${escapeHtml(item.title)}</span>
        <span class="legend-pill state-${safeItemState}">${safeItemState}</span>
      `;
      li.addEventListener("click", () => openInspector(item.id));
      listEl.appendChild(li);
    }
  };

  renderDepList(el("inspector-prereqs"), el("inspector-prereq-count"), task.prerequisites || []);
  renderDepList(el("inspector-dependents"), el("inspector-dep-count"), task.dependents || []);
}

function bindEvents() {
  syncLinkModeToggleUI();

  el("link-mode-app")?.addEventListener("click", () => setDiscordLinkMode("app"));
  el("link-mode-web")?.addEventListener("click", () => setDiscordLinkMode("web"));

  el("project-select").addEventListener("change", async (e) => {
    state.activeProjectId = e.target.value;
    el("task-inspector").classList.add("hidden");
    await loadActiveProjectData();
  });

  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.addEventListener("click", () => {
      for (const b of document.querySelectorAll(".tab-btn")) {
        b.classList.toggle("active", b === btn);
        b.setAttribute("aria-selected", b === btn ? "true" : "false");
      }
      state.activeView = btn.dataset.view;
      renderCurrentView();
    });
  }

  for (const id of ["filter-search", "filter-group-by", "filter-squad", "filter-tag", "filter-due", "filter-overdue-only"]) {
    el(id).addEventListener("input", () => renderCurrentView());
    el(id).addEventListener("change", () => renderCurrentView());
  }

  el("dag-orient-lr").addEventListener("click", () => {
    state.dagOrientation = "LR";
    el("dag-orient-lr").classList.add("active");
    el("dag-orient-tb").classList.remove("active");
    renderTechTree();
  });

  el("dag-orient-tb").addEventListener("click", () => {
    state.dagOrientation = "TB";
    el("dag-orient-tb").classList.add("active");
    el("dag-orient-lr").classList.remove("active");
    renderTechTree();
  });

  el("dag-critical-toggle").addEventListener("click", (e) => {
    state.highlightCriticalPath = !state.highlightCriticalPath;
    e.currentTarget.classList.toggle("active", state.highlightCriticalPath);
    renderTechTree();
  });

  el("dag-fit-btn").addEventListener("click", () => {
    if (state.cy) state.cy.fit(undefined, 30);
  });

  el("inspector-close").addEventListener("click", () => {
    el("task-inspector").classList.add("hidden");
  });
}

document.addEventListener("DOMContentLoaded", initPortal);
