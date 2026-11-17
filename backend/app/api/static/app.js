// SAYZO chat UI. Plain JavaScript, no build step. All server text is inserted with
// textContent, never innerHTML, so source content can never run as markup in the page.

const STATUS_LABELS = {
  ANSWERED: "Answered",
  INSUFFICIENT_EVIDENCE: "Not enough evidence",
  CONFLICTING_EVIDENCE: "Sources disagree",
  TASK_PROPOSED: "Task proposal",
  DENIED: "Not allowed",
  FAILED: "Failed",
};

const state = { config: null, supabase: null, token: null, devUser: null, conversationId: null };
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node[key] = value;
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (state.devUser) headers["X-Dev-User"] = state.devUser;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) { signOut(); throw new Error("Please sign in again."); }
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error((body && body.detail && JSON.stringify(body.detail).replace(/^"|"$/g, "")) || response.statusText);
  return body;
}

// -- sign in ----------------------------------------------------------------------------------

async function start() {
  state.config = await (await fetch("/api/config")).json();
  if (state.config.auth_mode === "dev") {
    $("supabase-fields").hidden = true;
    $("dev-fields").hidden = false;
    $("email").required = $("password").required = false;
    for (const person of state.config.dev_people) $("dev-person").append(el("option", { value: person, textContent: person }));
    const saved = sessionStorage.getItem("sayzo-dev-user");
    if (saved) { state.devUser = saved; return enterApp(); }
  } else {
    state.supabase = window.supabase.createClient(state.config.supabase_url, state.config.supabase_publishable_key);
    const { data } = await state.supabase.auth.getSession();
    if (data.session) { state.token = data.session.access_token; return enterApp(); }
    state.supabase.auth.onAuthStateChange((_event, session) => { state.token = session ? session.access_token : null; });
  }
  $("signin").hidden = false;
}

$("signin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("signin-error").textContent = "";
  try {
    if (state.config.auth_mode === "dev") {
      state.devUser = $("dev-person").value;
      sessionStorage.setItem("sayzo-dev-user", state.devUser);
    } else {
      const { data, error } = await state.supabase.auth.signInWithPassword({ email: $("email").value, password: $("password").value });
      if (error) throw error;
      state.token = data.session.access_token;
    }
    await enterApp();
  } catch (error) {
    $("signin-error").textContent = error.message;
    state.devUser = state.token = null;
  }
});

async function signOut() {
  sessionStorage.removeItem("sayzo-dev-user");
  if (state.supabase) await state.supabase.auth.signOut();
  state.token = state.devUser = state.conversationId = null;
  $("app").hidden = true;
  $("signin").hidden = false;
}
$("signout").addEventListener("click", signOut);

async function enterApp() {
  const me = await api("/api/me");
  $("me").textContent = `${me.full_name} · ${me.role} · ${me.organisation_name} · sees: ${me.access_groups.join(", ")}`;
  $("signin").hidden = true;
  $("app").hidden = false;
  newChat();
  await Promise.all([loadConversations(), loadTasks()]);
}

// -- conversations ----------------------------------------------------------------------------

function newChat() {
  state.conversationId = null;
  $("messages").replaceChildren(el("p", { class: "muted msg" }, "Ask a question. Answers only use sources you can see, with citations."));
  for (const li of $("conversations").children) li.classList.remove("active");
}
$("new-chat").addEventListener("click", newChat);

async function loadConversations() {
  const list = await api("/api/conversations");
  $("conversations").replaceChildren(...list.map((c) =>
    el("li", { title: c.title, class: c.id === state.conversationId ? "active" : "", onclick: () => openConversation(c.id) }, c.title)));
}

async function openConversation(id) {
  state.conversationId = id;
  const messages = await api(`/api/conversations/${id}/messages`);
  $("messages").replaceChildren(...messages.map((m) => renderMessage(m)));
  await loadConversations();
}

$("ask").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("question").value.trim();
  if (!message) return;
  $("send").disabled = true;
  if (!state.conversationId) $("messages").replaceChildren();
  $("messages").append(renderMessage({ role: "USER", content: message }));
  const thinking = el("p", { class: "muted msg" }, "Searching your sources…");
  $("messages").append(thinking);
  $("question").value = "";
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: state.conversationId, client_request_id: crypto.randomUUID() }),
    });
    state.conversationId = result.conversation_id;
    thinking.replaceWith(renderMessage(result.reply, result.proposed_tasks));
    await loadConversations();
  } catch (error) {
    thinking.replaceWith(el("p", { class: "error msg" }, error.message));
  } finally {
    $("send").disabled = false;
    $("messages").scrollTop = $("messages").scrollHeight;
  }
});
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("ask").requestSubmit(); }
});

function renderMessage(message, proposedTasks = null) {
  if (message.role === "USER") return el("div", { class: "msg user" }, el("div", { class: "bubble" }, message.content));
  const citations = message.citations || [];
  const citationNodes = new Map();
  const list = el("div", { class: "citations" }, citations.map((c) => {
    const node = el("div", { class: "citation" },
      el("div", { class: "title" }, `[${c.index}] ${c.source_title} (v${c.source_version})`),
      el("div", { class: "muted" }, c.excerpt),
      c.source_id ? null : el("div", { class: "muted" }, "This source has since been removed."));
    citationNodes.set(c.index, node);
    return node;
  }));
  // Turn [n] markers into links to their citation.
  const text = el("div");
  for (const part of message.content.split(/(\[\d+\])/)) {
    const match = part.match(/^\[(\d+)\]$/);
    if (match && citationNodes.has(Number(match[1]))) {
      const target = citationNodes.get(Number(match[1]));
      text.append(el("span", { class: "cite", onclick: () => { target.classList.add("flash"); target.scrollIntoView({ block: "nearest" }); setTimeout(() => target.classList.remove("flash"), 1200); } }, part));
    } else text.append(part);
  }
  return el("div", { class: "msg" }, el("div", { class: "bubble" },
    message.status ? el("span", { class: `badge ${message.status}` }, STATUS_LABELS[message.status] || message.status) : null,
    text,
    citations.length ? list : null,
    proposedTasks && proposedTasks.length ? renderProposal(message.id, proposedTasks) : null));
}

function renderProposal(messageId, drafts) {
  const rows = drafts.map((draft, index) => {
    const box = el("input", { type: "checkbox", checked: true });
    const result = el("div", { class: "result" });
    const details = [draft.assignee_name || "unassigned", draft.due_date ? `due ${draft.due_date}` : "no due date", draft.priority.toLowerCase()];
    const row = el("div", { class: "draft" }, box, el("div", {},
      el("div", {}, el("strong", {}, draft.title), ` — ${details.join(", ")}`),
      draft.evidence_title ? el("div", { class: "muted" }, `from ${draft.evidence_title}`) : null,
      draft.warnings.map((w) => el("div", { class: "warn" }, `⚠ ${w}`)),
      result));
    return { draft, index, box, result, node: row };
  });
  const confirm = el("button", { onclick: async () => {
    confirm.disabled = true;
    for (const row of rows.filter((r) => r.box.checked && !r.done)) {
      try {
        const { draft } = row;
        const created = await api("/api/tasks", {
          method: "POST",
          body: JSON.stringify({
            draft: { title: draft.title, description: draft.description, assignee_member_id: draft.assignee_member_id,
                     due_date: draft.due_date, priority: draft.priority, evidence_source_id: draft.evidence_source_id },
            // One key per proposed task: clicking twice, or retrying, cannot create duplicates.
            idempotency_key: `${messageId}:${row.index}`,
            conversation_id: state.conversationId,
            message_id: messageId,
          }),
        });
        row.result.textContent = created.replayed ? "✓ already created" : "✓ created";
        row.done = true;
        row.box.disabled = true;
      } catch (error) {
        row.result.className = "error";
        row.result.textContent = error.message;
      }
    }
    confirm.disabled = rows.every((r) => r.done);
    await loadTasks();
  } }, "Confirm selected tasks");
  return el("div", { class: "proposal" }, el("div", { class: "muted" }, "Nothing is created until you confirm."), rows.map((r) => r.node), confirm);
}

// -- tasks ------------------------------------------------------------------------------------

async function loadTasks() {
  const tasks = await api(`/api/tasks?mine=${$("mine").checked}`);
  $("task-list").replaceChildren(...(tasks.length ? tasks.map(renderTask) : [el("li", { class: "muted" }, "No tasks yet.")]));
}
$("mine").addEventListener("change", loadTasks);

function renderTask(task) {
  const select = el("select", { onchange: async () => {
    try { await api(`/api/tasks/${task.id}`, { method: "PATCH", body: JSON.stringify({ status: select.value }) }); }
    catch (error) { alertInline(error.message); }
    await loadTasks();
  } }, ["PENDING", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED"].map((s) => el("option", { value: s, selected: s === task.status, textContent: s.replace("_", " ").toLowerCase() })));
  const history = el("div", { class: "history" });
  const alertInline = (text) => { history.className = "error"; history.textContent = text; };
  return el("li", { class: "task" },
    el("div", { class: "t" }, task.title),
    el("div", { class: "muted" }, `${task.assigned_to_name || "unassigned"} · ${task.due_date ? "due " + task.due_date : "no due date"} · ${task.priority.toLowerCase()}`),
    task.evidence_title ? el("div", { class: "muted" }, `from ${task.evidence_title}`) : null,
    select,
    el("button", { class: "link", onclick: async () => {
      const events = await api(`/api/tasks/${task.id}/events`);
      history.className = "history";
      history.replaceChildren(...events.map((e) => el("div", {}, `${new Date(e.created_at).toLocaleString()} · ${e.event_type} by ${e.actor_name}`)));
    } }, " history"),
    history);
}

start().catch((error) => { $("signin").hidden = false; $("signin-error").textContent = error.message; });
