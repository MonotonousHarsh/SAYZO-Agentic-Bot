// SAYZO chat UI. Plain JavaScript, no build step.
// All server text is inserted with textContent, never innerHTML, so content from sources
// can never run as markup in the page.

const VERDICTS = {
  ANSWERED: "Answered from your sources",
  INSUFFICIENT_EVIDENCE: "Not enough evidence",
  CONFLICTING_EVIDENCE: "Sources disagree",
  TASK_PROPOSED: "Tasks to review",
  DENIED: "Not allowed for your role",
  FAILED: "Couldn't answer",
};
const SOURCE_TYPES = { message: "Message", document: "Document", meeting_transcript: "Meeting" };
const STATUSES = [
  ["PENDING", "To do"], ["IN_PROGRESS", "In progress"], ["BLOCKED", "Blocked"],
  ["COMPLETED", "Done"], ["CANCELLED", "Cancelled"],
];
// Starter questions for the demo companies; any other organisation gets the generic ones.
const SUGGESTIONS = {
  "Nexora Labs": [
    "Why was the Project Atlas launch moved, and what is the new date?",
    "How many pilot customers does Project Atlas have?",
    "Who owns the API migration for Atlas, and when is it due?",
    "Create tasks for the action items agreed in the Atlas weekly sync.",
  ],
  "Vertex Retail": ["When does Project Atlas go live?", "What did the last rollout meeting decide?"],
};
const GENERIC = ["What was decided in the most recent meeting?", "What changed in our plans this week?"];

const state = { config: null, supabase: null, token: null, devUser: null, me: null,
  conversationId: null, members: null, mine: false };
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key in node && !key.includes("-")) node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const formatDate = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const initials = (name) => name.split(" ").map((p) => p[0]).join("").slice(0, 2).toUpperCase();
const today = () => new Date().toISOString().slice(0, 10);

let toastTimer;
function toast(message, bad = false) {
  const node = $("toast");
  node.textContent = message;
  node.className = bad ? "toast bad" : "toast";
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 4000);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.supabase) {
    const { data } = await state.supabase.auth.getSession();  // refreshes expired tokens
    state.token = data.session ? data.session.access_token : null;
  }
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (state.devUser) headers["X-Dev-User"] = state.devUser;
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => null);
  if (response.status === 401) { await signOut(); throw new Error("Your session ended. Sign in again."); }
  if (!response.ok) {
    const detail = body && body.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : response.statusText);
  }
  return body;
}

// -- sign in ------------------------------------------------------------------------------

async function start() {
  state.config = await (await fetch("/api/config")).json();
  if (state.config.auth_mode === "dev") {
    $("signin-form").hidden = true;
    $("dev-signin").hidden = false;
    renderDevPeople();
    const saved = sessionStorage.getItem("sayzo-dev-user");
    if (saved) { state.devUser = saved; return enterApp(); }
  } else {
    if (!window.supabase) throw new Error("Couldn't load the sign-in library. Check your connection and reload.");
    state.supabase = window.supabase.createClient(state.config.supabase_url, state.config.supabase_publishable_key);
    const { data } = await state.supabase.auth.getSession();
    if (data.session) { state.token = data.session.access_token; return enterApp(); }
  }
  $("signin").hidden = false;
}

function renderDevPeople() {
  const byOrg = new Map();
  for (const person of state.config.dev_people) {
    if (!byOrg.has(person.organisation)) byOrg.set(person.organisation, []);
    byOrg.get(person.organisation).push(person);
  }
  $("dev-people").replaceChildren(...[...byOrg].map(([org, people]) =>
    el("div", { class: "org-group" }, el("h2", {}, org), people.map((p) =>
      el("button", { class: "person", type: "button", onclick: () => signInAs(p.key) },
        el("span", { class: "name" }, p.name),
        el("span", { class: "title" }, p.title || ""),
        el("span", { class: "role" }, p.role.toLowerCase()))))));
}

async function signInAs(key) {
  state.devUser = key;
  sessionStorage.setItem("sayzo-dev-user", key);
  try { await enterApp(); } catch (error) { $("signin-error").textContent = error.message; }
}

$("signin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("signin-error").textContent = "";
  const button = event.submitter;
  button.disabled = true;
  try {
    const { data, error } = await state.supabase.auth.signInWithPassword({
      email: $("email").value.trim(), password: $("password").value });
    if (error) throw new Error(error.message === "Invalid login credentials" ? "That email and password don't match an account." : error.message);
    state.token = data.session.access_token;
    await enterApp();
  } catch (error) {
    $("signin-error").textContent = error.message;
    if (state.supabase) await state.supabase.auth.signOut();
  } finally { button.disabled = false; }
});

async function signOut() {
  sessionStorage.removeItem("sayzo-dev-user");
  if (state.supabase) await state.supabase.auth.signOut();
  Object.assign(state, { token: null, devUser: null, me: null, conversationId: null, members: null });
  $("app").hidden = true;
  $("me-card").hidden = true;
  $("signin").hidden = false;
}

async function enterApp() {
  state.me = await api("/api/me");
  const me = state.me;
  $("org-name").textContent = me.organisation_name;
  $("me-button").replaceChildren(el("span", { class: "avatar" }, initials(me.full_name)),
    el("span", {}, me.full_name), el("span", { class: "role-tag" }, me.role.toLowerCase()));
  $("me-card").replaceChildren(
    el("dl", {},
      el("dt", {}, "Role"), el("dd", {}, roleText(me.role)),
      el("dt", {}, "Organisation"), el("dd", {}, me.organisation_name),
      el("dt", {}, "Can see"), el("dd", {}, me.access_groups.join(", "))),
    el("button", { class: "wide", onclick: signOut }, "Sign out"));
  $("signin").hidden = true;
  $("app").hidden = false;
  newChat();
  await Promise.all([loadConversations(), loadTasks()]);
}

const roleText = (role) => ({ ADMIN: "Admin: can ask, create and change any task",
  MEMBER: "Member: can ask, create tasks and change their own",
  VIEWER: "Viewer: can ask questions only" })[role] || role;

$("me-button").addEventListener("click", () => {
  const card = $("me-card");
  card.hidden = !card.hidden;
  $("me-button").setAttribute("aria-expanded", String(!card.hidden));
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".who")) { $("me-card").hidden = true; $("me-button").setAttribute("aria-expanded", "false"); }
});

// -- drawers on narrow screens -----------------------------------------------------------

function toggleDrawer(id, open) {
  const drawer = $(id);
  const show = open ?? !drawer.classList.contains("open");
  for (const other of ["chats", "tasks"]) $(other).classList.remove("open");
  drawer.classList.toggle("open", show);
  $("scrim").hidden = !show;
}
$("toggle-chats").addEventListener("click", () => toggleDrawer("chats"));
$("toggle-tasks").addEventListener("click", () => toggleDrawer("tasks"));
$("scrim").addEventListener("click", () => toggleDrawer("chats", false));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") toggleDrawer("chats", false); });

// -- conversations --------------------------------------------------------------------------

function newChat() {
  state.conversationId = null;
  const suggestions = SUGGESTIONS[state.me.organisation_name] || GENERIC;
  $("messages").replaceChildren(el("section", { class: "welcome" },
    el("h1", {}, `What do you need to know, ${state.me.full_name.split(" ")[0]}?`),
    el("p", {}, "SAYZO answers only from the messages, documents and meetings you're allowed to see, and shows its sources. If they don't cover your question, it says so."),
    el("div", { class: "suggestions" }, suggestions.map((q) =>
      el("button", { type: "button", onclick: () => ask(q) }, q)))));
  markCurrentConversation();
  toggleDrawer("chats", false);
  $("question").focus();
}
$("new-chat").addEventListener("click", newChat);

async function loadConversations() {
  const list = await api("/api/conversations");
  $("conversations").replaceChildren(...(list.length
    ? list.map((c) => el("li", {}, el("button", { title: c.title, "data-id": c.id, onclick: () => openConversation(c.id) }, c.title)))
    : [el("li", { class: "empty" }, "Your questions will appear here.")]));
  markCurrentConversation();
}

function markCurrentConversation() {
  for (const button of $("conversations").querySelectorAll("button")) {
    button.setAttribute("aria-current", String(button.dataset.id === state.conversationId));
  }
}

async function openConversation(id) {
  try {
    const messages = await api(`/api/conversations/${id}/messages`);
    state.conversationId = id;
    $("messages").replaceChildren(...pairUp(messages));
    markCurrentConversation();
    toggleDrawer("chats", false);
  } catch (error) { toast(error.message, true); }
}

function pairUp(messages) {
  const turns = [];
  let current = null;
  for (const m of messages) {
    if (m.role === "USER") {
      current = el("div", { class: "turn" }, renderQuestion(m.content));
      turns.push(current);
    } else {
      if (!current) { current = el("div", { class: "turn" }); turns.push(current); }
      current.append(renderAnswer(m));
      current = null;
    }
  }
  return turns;
}

// -- asking ---------------------------------------------------------------------------------

$("ask").addEventListener("submit", (event) => { event.preventDefault(); ask($("question").value); });
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("ask").requestSubmit(); }
});
$("question").addEventListener("input", () => {
  const box = $("question");
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, 180)}px`;
});

async function ask(text) {
  const message = text.trim();
  if (!message || $("send").disabled) return;
  $("send").disabled = true;
  $("question").value = "";
  $("question").style.height = "auto";
  if (!state.conversationId) $("messages").replaceChildren();
  const thinking = el("p", { class: "thinking" }, "Searching the sources you can see");
  const turn = el("div", { class: "turn" }, renderQuestion(message), thinking);
  $("messages").append(turn);
  turn.scrollIntoView({ block: "end" });
  try {
    const result = await api("/api/chat", { method: "POST", body: JSON.stringify({
      message, conversation_id: state.conversationId, client_request_id: crypto.randomUUID() }) });
    state.conversationId = result.conversation_id;
    thinking.replaceWith(renderAnswer(result.reply, result.proposed_tasks));
    loadConversations();
  } catch (error) {
    thinking.replaceWith(el("p", { class: "error" }, error.message));
    $("question").value = message;
  } finally {
    $("send").disabled = false;
    $("question").focus();
  }
}

function renderQuestion(text) {
  return el("div", { class: "question" }, el("p", {}, text));
}

function renderAnswer(message, proposedTasks = null) {
  const status = message.status || "ANSWERED";
  const sources = new Map();
  const sourceList = message.citations.map((c) => {
    const type = SOURCE_TYPES[c.source_type];
    const card = el("li", { class: c.source_id ? "source" : "source gone", id: `src-${message.id}-${c.index}` },
      el("span", { class: "n" }, c.index),
      el("span", { class: "title" }, c.source_title),
      el("span", { class: "meta" },
        type && el("span", {}, type),
        c.location && !c.location.includes(":") && el("span", {}, c.location),
        c.occurred_at && el("span", {}, formatDate(c.occurred_at)),
        el("span", {}, `version ${c.source_version}`),
        !c.source_id && el("span", {}, "since removed")),
      el("p", { class: "excerpt" }, c.excerpt.replace(/(^|\s)#{1,6}\s/g, "$1")));
    sources.set(c.index, card);
    return card;
  });

  // With an editable proposal below, the stored text list would only repeat it.
  const content = proposedTasks && proposedTasks.length
    ? `I found ${proposedTasks.length === 1 ? "1 action item" : `${proposedTasks.length} action items`} to turn into tasks.`
    : message.content;
  // Citation markers [n] become buttons that light up their source.
  const text = el("p", { class: "answer-text" });
  for (const part of content.split(/(\[\d+\])/)) {
    const match = part.match(/^\[(\d+)\]$/);
    const card = match && sources.get(Number(match[1]));
    if (!card) { text.append(part); continue; }
    text.append(el("button", { class: "mark", type: "button", "aria-label": `Source ${match[1]}`,
      onclick: (event) => {
        for (const lit of document.querySelectorAll(".source.lit")) lit.classList.remove("lit");
        card.classList.add("lit");
        card.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        event.currentTarget.blur();
      } }, match[1]));
  }

  return el("article", { class: `answer ${status}` },
    el("p", { class: "verdict" }, VERDICTS[status] || status),
    text,
    sourceList.length > 0 && el("section", { class: "sources" },
      el("h3", {}, sourceList.length === 1 ? "Source" : `${sourceList.length} sources`),
      el("ol", { class: "plain" }, sourceList)),
    proposedTasks && proposedTasks.length > 0 && renderProposal(message.id, proposedTasks));
}

// -- task proposals -------------------------------------------------------------------------

async function members() {
  if (!state.members) state.members = await api("/api/members");
  return state.members;
}

function renderProposal(messageId, drafts) {
  const container = el("section", { class: "proposal" },
    el("p", { class: "proposal-note" }, "Check the details, change anything that's wrong, then create the tasks you want. Nothing exists until you do."));
  const rows = drafts.map((draft, index) => {
    const row = { draft, index, done: false };
    row.include = el("input", { type: "checkbox", checked: true, "aria-label": "Include this task", onchange: updateButton });
    row.title = el("input", { class: "draft-title", value: draft.title, maxLength: 200, "aria-label": "Task title" });
    row.assignee = el("select", {}, el("option", { value: "" }, "Unassigned"));
    row.due = el("input", { type: "date", value: draft.due_date || "" });
    row.priority = el("select", {}, ["LOW", "MEDIUM", "HIGH", "URGENT"].map((p) =>
      el("option", { value: p, selected: p === draft.priority }, p[0] + p.slice(1).toLowerCase())));
    row.outcome = el("p", { class: "outcome" });
    row.node = el("div", { class: "draft" }, row.include, el("div", {},
      row.title,
      el("div", { class: "draft-fields" },
        el("label", {}, "Assignee", row.assignee),
        el("label", {}, "Due", row.due),
        el("label", {}, "Priority", row.priority)),
      draft.evidence_title && el("p", { class: "from" }, `From ${draft.evidence_title}`),
      draft.warnings.map((w) => el("p", { class: "warning" }, w)),
      row.outcome));
    return row;
  });
  members().then((list) => {
    for (const row of rows) {
      row.assignee.append(...list.map((m) => el("option", { value: m.member_id, selected: m.member_id === row.draft.assignee_member_id }, m.full_name)));
    }
  }).catch(() => {});

  const create = el("button", { class: "primary", type: "button", onclick: confirm });
  function updateButton() {
    if (rows.every((r) => r.done || !r.include.checked) && rows.some((r) => r.done)) {
      create.replaceWith(el("p", { class: "proposal-note" }, "Done. ",
        el("button", { class: "text", type: "button", onclick: () => showTasksPanel() }, "View tasks")));
      return;
    }
    const count = rows.filter((r) => !r.done && r.include.checked).length;
    create.textContent = count === 1 ? "Create 1 task" : `Create ${count} tasks`;
    create.disabled = count === 0;
  }
  updateButton();

  async function confirm() {
    create.disabled = true;
    const created = [];
    for (const row of rows.filter((r) => !r.done && r.include.checked)) {
      try {
        const result = await api("/api/tasks", { method: "POST", body: JSON.stringify({
          draft: {
            title: row.title.value.trim(), description: row.draft.description,
            assignee_member_id: row.assignee.value || null, due_date: row.due.value || null,
            priority: row.priority.value, evidence_source_id: row.draft.evidence_source_id },
          // One key per proposed task: a double click or a retry can't create a duplicate.
          idempotency_key: `${messageId}:${row.index}`,
          conversation_id: state.conversationId, message_id: messageId }) });
        row.done = true;
        created.push(result.task);
        row.outcome.className = "outcome";
        row.outcome.textContent = result.replayed ? "Already created" : "Created";
        row.node.classList.add("done");
        for (const input of [row.include, row.title, row.assignee, row.due, row.priority]) input.disabled = true;
      } catch (error) {
        row.outcome.className = "outcome failed";
        row.outcome.textContent = error.message;
      }
    }
    updateButton();
    if (created.length) await revealTasks(created);
  }

  container.append(...rows.map((r) => r.node), el("div", { class: "proposal-actions" }, create));
  return container;
}

// -- tasks panel ----------------------------------------------------------------------------

function setMine(mine) {
  state.mine = mine;
  $("tasks-all").classList.toggle("on", !mine);
  $("tasks-mine").classList.toggle("on", mine);
  $("tasks-all").setAttribute("aria-pressed", String(!mine));
  $("tasks-mine").setAttribute("aria-pressed", String(mine));
  return loadTasks();
}
for (const [id, mine] of [["tasks-all", false], ["tasks-mine", true]]) {
  $(id).addEventListener("click", () => setMine(mine));
}

// Below 1100px the Tasks panel is a drawer; on a wide screen it is always on show.
const panelIsDrawer = () => window.matchMedia("(max-width: 1100px)").matches;
function showTasksPanel() { if (panelIsDrawer()) toggleDrawer("tasks", true); }

// After tasks are created: make sure they are in the list the person is looking at, and
// make the panel visible so the result is never hidden behind a closed drawer.
async function revealTasks(created) {
  state.fresh = new Set(created.map((t) => t.id));
  const mine = (t) => t.assigned_to === state.me.member_id;
  if (state.mine && !created.every(mine)) await setMine(false);  // "Mine" would hide them
  else await loadTasks();
  const label = created.length === 1 ? "Created 1 task" : `Created ${created.length} tasks`;
  toast(label);
  showTasksPanel();
  document.querySelector(".task.fresh")?.scrollIntoView({ block: "nearest" });
  setTimeout(() => { state.fresh = null; document.querySelectorAll(".task.fresh").forEach((n) => n.classList.remove("fresh")); }, 8000);
}

let loadSeq = 0;
async function loadTasks() {
  const seq = ++loadSeq;  // a slower, older response must never overwrite a newer one
  try {
    const tasks = await api(`/api/tasks?mine=${state.mine}`);
    if (seq !== loadSeq) return;
    if (!tasks.length) {
      $("task-groups").replaceChildren(el("p", { class: "empty" }, state.mine
        ? "Nothing is assigned to you."
        : "No tasks yet. Ask SAYZO to create tasks from a meeting's action items."));
      return;
    }
    const groups = STATUSES
      .map(([status, label]) => [label, tasks.filter((t) => t.status === status)])
      .filter(([, group]) => group.length > 0);
    $("task-groups").replaceChildren(...groups.map(([label, group]) =>
      el("section", { class: "task-group" },
        el("h3", {}, `${label} (${group.length})`), el("ul", {}, group.map(renderTask)))));
  } catch (error) { toast(error.message, true); }
}

function renderTask(task) {
  const overdue = task.due_date && task.due_date < today() && !["COMPLETED", "CANCELLED"].includes(task.status);
  const history = el("ul", { class: "history" });
  const status = el("select", { "aria-label": "Status", onchange: async () => {
    try {
      await api(`/api/tasks/${task.id}`, { method: "PATCH", body: JSON.stringify({ status: status.value }) });
      toast(`Moved to ${STATUSES.find(([s]) => s === status.value)[1].toLowerCase()}`);
    } catch (error) { toast(error.message, true); }
    loadTasks();
  } }, STATUSES.map(([value, label]) => el("option", { value, selected: value === task.status }, label)));
  return el("li", { class: `task ${task.status}${state.fresh?.has(task.id) ? " fresh" : ""}` },
    el("div", { class: "t" }, task.title),
    el("div", { class: "meta" },
      task.assigned_to_name || "Unassigned", " · ",
      task.due_date ? el("span", { class: overdue ? "overdue" : "" }, `${overdue ? "overdue, " : "due "}${formatDate(task.due_date)}`) : "no due date",
      task.priority !== "MEDIUM" && ` · ${task.priority.toLowerCase()} priority`),
    task.evidence_title && el("div", { class: "meta" }, `From ${task.evidence_title}`),
    el("div", { class: "task-controls" }, status,
      el("button", { class: "text", type: "button", onclick: async () => {
        if (history.childElementCount) { history.replaceChildren(); return; }
        try {
          const events = await api(`/api/tasks/${task.id}/events`);
          history.replaceChildren(...events.map((e) => el("li", {}, describeEvent(e))));
        } catch (error) { toast(error.message, true); }
      } }, "History")),
    history);
}

function describeEvent(event) {
  const when = new Date(event.created_at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  if (event.event_type === "CREATED") return `${when}: created by ${event.actor_name}`;
  if (event.event_type === "STATUS_CHANGED") {
    const label = (s) => (STATUSES.find(([v]) => v === s) || [s, s])[1].toLowerCase();
    return `${when}: ${event.actor_name} moved it from ${label(event.old_value.status)} to ${label(event.new_value.status)}`;
  }
  return `${when}: ${event.event_type.toLowerCase()} by ${event.actor_name}`;
}

start().catch((error) => { $("signin").hidden = false; $("signin-error").textContent = error.message; });
