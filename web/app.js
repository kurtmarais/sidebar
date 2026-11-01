// Sidebar phone app: view and edit the sidebar's text cards from a phone.
// Data lives in your Supabase project; this app keeps a copy on the phone so it
// opens instantly and works offline (edits upload when you're back online).
// Conflicts: newest edit wins, per card - the same rule as the desktop app.

import { Supabase, AuthError } from "./supabase.js";
import { SUPABASE_URL, SUPABASE_KEY } from "./config.js";

const STORE = "sidebar.phone.v1";
const TYPES = { note: "Note", checklist: "Checklist", code: "Code", links: "Links" };
const TYPE_COLOR = { note: "yellow", checklist: "blue", code: "purple", links: "teal" };
const COLORS = {
  yellow: "#fcc803", orange: "#ff9f43", red: "#ff6b6b", pink: "#f78fb3", purple: "#a78bfa",
  blue: "#4d8dff", teal: "#2dd4bf", green: "#4ade80", grey: "#9aa3bf",
};
const PRIORITY = { 0: "None", 1: "Low", 2: "Medium", 3: "High" };
const PRIORITY_COLOR = { 1: "#4d8dff", 2: "#ff9f43", 3: "#ff6b6b" };
const CARD_FIELDS = ["uid", "board_uid", "type", "title", "color", "content", "position", "collapsed",
  "priority", "due_date", "remind_at", "archived", "archived_at", "link_app", "link_title",
  "created_at", "updated_at", "deleted"];
const BOARD_FIELDS = ["uid", "name", "position", "updated_at", "deleted"];

// ------------------------------------------------------------------ state
const blank = () => ({
  url: "", key: "", session: null, boards: {}, cards: {},
  cursors: { boards: "", cards: "" }, queue: { boards: {}, cards: {} },
  view: "notes", board: "", lastSync: 0,
});
let S = load();
let syncing = false;
let editing = null;          // uid of the card open in the editor
let query = "";
let status = "";

function load() {
  try { return { ...blank(), ...JSON.parse(localStorage.getItem(STORE) || "{}") }; }
  catch { return blank(); }
}
function save() { localStorage.setItem(STORE, JSON.stringify(S)); }
const now = () => Date.now() / 1000;
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()) + Date.now()).replace(/[^a-z0-9]/gi, "").slice(0, 32);
const config = () => ({ url: SUPABASE_URL || S.url, key: SUPABASE_KEY || S.key });
const client = () => new Supabase(config().url, config().key);

// ------------------------------------------------------------------- DOM
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));   // never innerHTML
  }
  return el;
}
const $ = (sel) => document.querySelector(sel);

// ------------------------------------------------------------------- data
const boards = () => Object.values(S.boards).filter((b) => !b.deleted).sort((a, b) => a.position - b.position);
const liveCards = () => Object.values(S.cards).filter((c) => !c.deleted)
  .sort((a, b) => a.position - b.position || a.created_at - b.created_at);
const boardName = (u) => (S.boards[u] && S.boards[u].name) || "";

function pick(row, fields) {
  const out = {};
  for (const f of fields) out[f] = row[f] === undefined ? null : row[f];
  return out;
}

function touch(table, row) {
  row.updated_at = now();
  S[table][row.uid] = row;
  S.queue[table][row.uid] = pick(row, table === "cards" ? CARD_FIELDS : BOARD_FIELDS);
  save();
  schedulePush();
}

function newCard(type) {
  const first = boards()[0];
  const minPos = Math.min(0, ...liveCards().map((c) => c.position));
  const content = { note: { text: "" }, checklist: { items: [] }, code: { text: "", language: "text" },
    links: { links: [] } }[type];
  const t = now();
  const card = {
    uid: uid(), board_uid: S.board || (first ? first.uid : ""), type, title: "", color: TYPE_COLOR[type],
    content, position: minPos - 1, collapsed: false, priority: 0, due_date: null, remind_at: null,
    archived: false, archived_at: null, link_app: "", link_title: "", created_at: t, updated_at: t, deleted: false,
  };
  touch("cards", card);
  S.view = "notes";
  query = "";
  openEditor(card.uid);
}

// ------------------------------------------------------------------- sync
let pushTimer = null;
function schedulePush() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => syncNow(), 900);
}

async function token(sb, force = false) {
  if (!S.session) throw new AuthError("Not signed in");
  if (force || S.session.expires_at - now() < 120) {
    S.session = await sb.refresh(S.session.refresh_token);   // refresh tokens rotate: keep the new one
    save();
  }
  return S.session.access_token;
}

function merge(table, rows) {
  let changed = false;
  for (const r of rows) {
    const local = S[table][r.uid];
    const queued = S.queue[table][r.uid];
    if (queued && queued.updated_at > r.updated_at) continue;       // our newer edit will upload
    if (local && r.updated_at <= local.updated_at && !r.deleted) continue;
    if (r.deleted) delete S[table][r.uid];
    else if (!(table === "cards" && r.uid === editing)) S[table][r.uid] = r;
    if (queued) delete S.queue[table][r.uid];
    changed = true;
  }
  if (rows.length) S.cursors[table] = rows[rows.length - 1].server_updated_at;
  return changed;
}

async function syncNow() {
  if (syncing || !S.session) return;
  if (!navigator.onLine) { setStatus("Offline – changes will upload later"); return; }
  syncing = true;
  setStatus("Syncing…");
  const sb = client();
  try {
    let tok = await token(sb);
    let pulledB, pulledC;
    try {
      pulledB = await sb.changesSince("boards", S.cursors.boards, tok);
    } catch (e) {
      if (!(e instanceof AuthError)) throw e;
      tok = await token(sb, true);                             // access token expired early
      pulledB = await sb.changesSince("boards", S.cursors.boards, tok);
    }
    pulledC = await sb.changesSince("cards", S.cursors.cards, tok);
    const changed = merge("boards", pulledB) | merge("cards", pulledC);
    for (const table of ["boards", "cards"]) {
      const rows = Object.values(S.queue[table]);
      if (!rows.length) continue;
      await sb.upsert(table, rows, tok);
      for (const r of rows) {
        if (S.queue[table][r.uid] && S.queue[table][r.uid].updated_at === r.updated_at) delete S.queue[table][r.uid];
      }
    }
    S.lastSync = now();
    save();
    setStatus("");
    if (changed) renderList();
  } catch (e) {
    if (e instanceof AuthError) {
      S.session = null;
      save();
      render();
      showLoginError("Please sign in again.");
    } else {
      setStatus("Offline – changes will upload later");
    }
  } finally {
    syncing = false;
  }
}

function setStatus(text) {
  status = text;
  const el = $("#status");
  if (!el) return;
  const pending = Object.keys(S.queue.cards).length + Object.keys(S.queue.boards).length;
  const last = S.lastSync ? new Date(S.lastSync * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
  el.textContent = text || (pending ? `${pending} change${pending > 1 ? "s" : ""} waiting` : last ? `Synced ${last}` : "");
}

// ------------------------------------------------------------- dates
const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayIso = () => isoDate(new Date());
const tomorrowIso = () => { const d = new Date(); d.setDate(d.getDate() + 1); return isoDate(d); };

function dueBadge(c) {
  if (!c.due_date) return null;
  if (c.due_date < todayIso()) return ["Overdue", "var(--danger)"];
  if (c.due_date === todayIso()) return ["Today", "var(--accent)"];
  if (c.due_date === tomorrowIso()) return ["Tomorrow", "var(--info)"];
  const d = new Date(c.due_date + "T12:00:00");
  return ["Due " + d.toLocaleDateString([], { day: "numeric", month: "short" }), "var(--muted)"];
}

function remindText(ts) {
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const day = isoDate(d);
  return day === todayIso() ? time : day === tomorrowIso() ? `Tomorrow ${time}`
    : `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}

function todaySection(c) {
  const t = todayIso(), tm = tomorrowIso();
  const rem = c.remind_at ? new Date(c.remind_at * 1000) : null;
  const remDay = rem ? isoDate(rem) : null;
  if ((c.due_date && c.due_date < t) || (rem && rem < new Date())) return "Overdue";
  if (c.due_date === t || remDay === t) return "Today";
  if (c.due_date === tm || remDay === tm) return "Tomorrow";
  return null;
}

function toLocalInput(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${isoDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ------------------------------------------------------------ rendering
function searchText(c) {
  const k = c.content || {};
  const parts = [c.title, k.text || "", ...(k.items || []).map((i) => i.text), ...(k.links || []).flatMap((l) => [l.label, l.url])];
  return parts.join("\n").toLowerCase();
}

function visibleGroups() {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const match = (c) => terms.every((t) => searchText(c).includes(t));
  const inBoard = (c) => !S.board || c.board_uid === S.board;
  const cards = liveCards().filter(match);
  if (S.view === "archive") {
    return [[null, cards.filter((c) => c.archived && inBoard(c)).sort((a, b) => (b.archived_at || 0) - (a.archived_at || 0))]];
  }
  const live = cards.filter((c) => !c.archived);
  if (S.view === "today") {
    const groups = { Overdue: [], Today: [], Tomorrow: [] };
    for (const c of live) { const s = todaySection(c); if (s) groups[s].push(c); }
    return Object.entries(groups).filter(([, v]) => v.length);
  }
  return [[null, live.filter(inBoard)]];
}

function linkHref(url) {
  const u = (url || "").trim();
  if (!u) return null;
  const full = /^[a-z][a-z0-9+.-]*:/i.test(u) ? u : `https://${u}`;
  return /^https?:/i.test(full) ? full : null;          // only web links are clickable
}

function cardPreview(c) {
  const k = c.content || {};
  if (c.type === "checklist") {
    const items = k.items || [];
    return h("div", { class: "items" }, items.map((it, i) => h("label", { class: "item" + (it.done ? " done" : ""),
      onclick: (e) => e.stopPropagation() },
      h("input", { type: "checkbox", checked: it.done, onchange: (e) => {
        const card = structuredClone(S.cards[c.uid]);
        card.content.items[i].done = e.target.checked;
        touch("cards", card);
        renderList();
      } }),
      h("span", {}, it.text))),
      items.length ? null : h("div", { class: "muted" }, "No items"));
  }
  if (c.type === "links") {
    return h("div", { class: "links" }, (k.links || []).map((l) => {
      const href = linkHref(l.url);
      return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer", onclick: (e) => e.stopPropagation() },
        l.label || l.url) : h("span", {}, l.label || l.url);
    }));
  }
  if (c.type === "code") return h("pre", { class: "code" }, (k.text || "").split("\n").slice(0, 8).join("\n"));
  const text = (k.text || "").trim();
  return text ? h("div", { class: "text" }, text.length > 400 ? text.slice(0, 400) + "…" : text) : null;
}

function cardEl(c) {
  const badges = [];
  if (c.priority) badges.push(h("span", { class: "badge", style: { color: PRIORITY_COLOR[c.priority] } }, PRIORITY[c.priority]));
  const due = dueBadge(c);
  if (due) badges.push(h("span", { class: "badge", style: { color: due[1] } }, due[0]));
  if (c.remind_at) badges.push(h("span", { class: "badge" }, "⏰ " + remindText(c.remind_at)));
  if (S.view !== "notes" || !S.board) {
    const b = boardName(c.board_uid);
    if (b) badges.push(h("span", { class: "badge muted" }, b));
  }
  return h("article", { class: "card", style: { "--c": COLORS[c.color] || COLORS.yellow },
    onclick: () => openEditor(c.uid) },
    h("div", { class: "card-title" }, c.title || h("span", { class: "muted" }, TYPES[c.type] || "Card")),
    badges.length ? h("div", { class: "badges" }, badges) : null,
    cardPreview(c),
    c.archived ? h("button", { class: "pill", onclick: (e) => { e.stopPropagation(); archive(c.uid, false); } }, "Restore") : null);
}

function renderList() {
  const list = $("#list");
  if (!list) return;
  list.replaceChildren();
  let count = 0;
  for (const [title, cards] of visibleGroups()) {
    if (title && cards.length) list.append(h("div", { class: "section" }, title.toUpperCase()));
    for (const c of cards) { list.append(cardEl(c)); count++; }
  }
  if (!count) {
    list.append(h("div", { class: "empty" }, query ? "No notes match your search."
      : S.view === "today" ? "Nothing due or reminding you today."
      : S.view === "archive" ? "The archive is empty." : "No notes here yet. Tap + to add one."));
  }
  for (const b of document.querySelectorAll(".views button")) b.classList.toggle("on", b.dataset.view === S.view);
  renderChips();
  setStatus(status);
}

function renderChips() {
  const chips = $("#chips");
  if (!chips) return;
  chips.replaceChildren(
    h("button", { class: "chip" + (!S.board ? " on" : ""), onclick: () => { S.board = ""; save(); renderList(); } }, "All"),
    ...boards().map((b) => h("button", {
      class: "chip" + (S.board === b.uid ? " on" : ""),
      onclick: () => { S.board = b.uid; save(); renderList(); },
      oncontextmenu: (e) => { e.preventDefault(); boardMenu(b); },
    }, b.name)),
    h("button", { class: "chip add", title: "New category", onclick: addBoard }, "+"));
}

function addBoard() {
  const name = (prompt("New category name:") || "").trim();
  if (!name) return;
  const pos = Math.max(-1, ...boards().map((b) => b.position)) + 1;
  touch("boards", { uid: uid(), name, position: pos, updated_at: now(), deleted: false });
  renderList();
}

function boardMenu(b) {
  const action = prompt(`Category “${b.name}”: type a new name to rename it, or DELETE to delete it with its notes.`, b.name);
  if (action === null) return;
  if (action.trim() === "DELETE") {
    if (boards().length <= 1) return alert("At least one category must remain.");
    for (const c of liveCards().filter((c) => c.board_uid === b.uid)) touch("cards", { ...c, deleted: true });
    touch("boards", { ...b, deleted: true });
    delete S.boards[b.uid];
    for (const c of Object.values(S.cards)) if (c.deleted) delete S.cards[c.uid];
    if (S.board === b.uid) S.board = "";
    save();
  } else if (action.trim() && action.trim() !== b.name) {
    touch("boards", { ...b, name: action.trim() });
  }
  renderList();
}

function archive(cardUid, on) {
  const c = structuredClone(S.cards[cardUid]);
  c.archived = on;
  c.archived_at = on ? now() : null;
  touch("cards", c);
  renderList();
}

// ---------------------------------------------------------------- editor
function openEditor(cardUid) {
  editing = cardUid;
  const c = structuredClone(S.cards[cardUid]);
  let dirty = false;
  let timer = null;
  const commit = () => { if (dirty) { touch("cards", c); dirty = false; } };
  const change = () => { dirty = true; clearTimeout(timer); timer = setTimeout(commit, 600); };

  const body = h("div", { class: "editor-body" });
  const drawBody = () => {
    body.replaceChildren();
    const k = c.content;
    if (c.type === "note") {
      body.append(h("textarea", { class: "big", placeholder: "Write something…", value: k.text || "",
        oninput: (e) => { k.text = e.target.value; change(); } }));
    } else if (c.type === "code") {
      body.append(h("select", { onchange: (e) => { k.language = e.target.value; change(); } },
        ["text", "python", "javascript", "sql", "json"].map((l) => h("option", { value: l, selected: (k.language || "text") === l }, l))),
      h("textarea", { class: "big mono", spellcheck: "false", value: k.text || "",
        oninput: (e) => { k.text = e.target.value; change(); } }));
    } else if (c.type === "checklist") {
      k.items = k.items || [];
      k.items.forEach((it, i) => body.append(h("div", { class: "row" },
        h("input", { type: "checkbox", checked: it.done, onchange: (e) => { it.done = e.target.checked; change(); } }),
        h("input", { class: "grow", value: it.text, oninput: (e) => { it.text = e.target.value; change(); } }),
        h("button", { class: "x", "aria-label": "Remove", onclick: () => { k.items.splice(i, 1); change(); drawBody(); } }, "×"))));
      const add = h("input", { class: "grow", placeholder: "Add item", enterkeyhint: "enter",
        onkeydown: (e) => { if (e.key === "Enter" && add.value.trim()) {
          k.items.push({ text: add.value.trim(), done: false }); change(); drawBody(); body.querySelector("input[placeholder='Add item']").focus(); } } });
      body.append(h("div", { class: "row" }, h("span", { class: "plus" }, "+"), add));
    } else if (c.type === "links") {
      k.links = k.links || [];
      k.links.forEach((l, i) => body.append(h("div", { class: "row link" },
        h("div", { class: "grow col" },
          h("input", { placeholder: "Label", value: l.label, oninput: (e) => { l.label = e.target.value; change(); } }),
          h("input", { placeholder: "https://…", inputmode: "url", value: l.url, oninput: (e) => { l.url = e.target.value; change(); } })),
        h("button", { class: "x", "aria-label": "Remove", onclick: () => { k.links.splice(i, 1); change(); drawBody(); } }, "×"))));
      body.append(h("button", { class: "pill", onclick: () => { k.links.push({ label: "", url: "" }); change(); drawBody(); } }, "+ Add link"));
    }
  };
  drawBody();

  const close = () => { clearTimeout(timer); commit(); editing = null; $("#editor").remove(); renderList(); };
  const sheet = h("div", { id: "editor", class: "sheet" },
    h("div", { class: "sheet-bar" },
      h("button", { class: "pill", onclick: close }, "Done"),
      h("span", { class: "muted" }, TYPES[c.type]),
      h("button", { class: "pill danger", onclick: () => {
        if (!confirm("Delete this card permanently?")) return;
        clearTimeout(timer); editing = null; touch("cards", { ...c, deleted: true }); delete S.cards[c.uid]; save();
        $("#editor").remove(); renderList(); } }, "Delete")),
    h("input", { class: "title", placeholder: "Title", value: c.title, oninput: (e) => { c.title = e.target.value; change(); } }),
    body,
    h("div", { class: "meta" },
      h("label", {}, "Category", h("select", { onchange: (e) => { c.board_uid = e.target.value; change(); } },
        boards().map((b) => h("option", { value: b.uid, selected: b.uid === c.board_uid }, b.name)))),
      h("label", {}, "Priority", h("select", { onchange: (e) => { c.priority = Number(e.target.value); change(); } },
        [3, 2, 1, 0].map((p) => h("option", { value: p, selected: c.priority === p }, PRIORITY[p])))),
      h("label", {}, "Due date", h("input", { type: "date", value: c.due_date || "",
        onchange: (e) => { c.due_date = e.target.value || null; change(); } })),
      h("label", {}, "Reminder", h("input", { type: "datetime-local", value: toLocalInput(c.remind_at),
        onchange: (e) => { c.remind_at = e.target.value ? new Date(e.target.value).getTime() / 1000 : null; change(); } })),
      h("div", { class: "swatches" }, Object.entries(COLORS).map(([name, hex]) => h("button", {
        class: "swatch" + (c.color === name ? " on" : ""), style: { background: hex }, "aria-label": name,
        onclick: (e) => { c.color = name; change(); for (const s of e.target.parentNode.children) s.classList.remove("on"); e.target.classList.add("on"); } })))),
    h("button", { class: "pill wide", onclick: () => { c.archived = !c.archived; c.archived_at = c.archived ? now() : null; dirty = true; close(); } },
      c.archived ? "Restore from archive" : "Mark done (archive)"));
  document.body.append(sheet);
  if (!c.title && c.type !== "checklist") sheet.querySelector(".title").focus();
}

// ------------------------------------------------------------- screens
function showLoginError(msg) { const el = $("#login-error"); if (el) el.textContent = msg; }

function setupScreen() {
  // Only ask for what config.js doesn't already provide.
  const url = h("input", { placeholder: "https://xxxx.supabase.co", inputmode: "url", value: config().url,
    class: SUPABASE_URL ? "hidden" : null });
  const key = h("input", { placeholder: "Publishable (anon) key", value: config().key,
    class: SUPABASE_KEY ? "hidden" : null });
  return h("main", { class: "login" }, h("h1", {}, "Sidebar"),
    h("p", { class: "muted" }, "Connect to your Supabase project. Find these under Project Settings → API Keys / Data API in Supabase. You only need to do this once."),
    url, key,
    h("button", { class: "primary", onclick: () => {
      if (!/^https:\/\//.test(url.value.trim()) || !key.value.trim()) return alert("Enter the https:// project URL and the publishable key.");
      S.url = url.value.trim(); S.key = key.value.trim(); save(); render(); } }, "Continue"));
}

function loginScreen() {
  const email = h("input", { type: "email", placeholder: "Email", autocomplete: "username", value: S.session?.email || "" });
  const pw = h("input", { type: "password", placeholder: "Password", autocomplete: "current-password" });
  const go = async () => {
    showLoginError("Signing in…");
    try {
      S.session = await client().signIn(email.value.trim(), pw.value);
      save();
      render();
      await syncNow();
    } catch (e) {
      showLoginError(e.message || "Could not sign in");
    }
  };
  pw.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  return h("main", { class: "login" }, h("h1", {}, "Sidebar"),
    h("p", { class: "muted" }, "Sign in with the account you use in the sidebar's Phone sync."),
    email, pw, h("button", { class: "primary", onclick: go }, "Sign in"),
    h("p", { id: "login-error", class: "error" }),
    SUPABASE_URL ? null : h("button", { class: "link", onclick: () => { S.url = ""; S.key = ""; save(); render(); } }, "Change Supabase project"));
}

function mainScreen() {
  const signOut = () => {
    if (!confirm("Sign out and remove the notes stored on this phone? They stay in Supabase and on your PC.")) return;
    const keep = { url: S.url, key: S.key };
    S = { ...blank(), ...keep };
    save();
    render();
  };
  return h("main", { class: "app" },
    h("header", {},
      h("h1", {}, "Sidebar"),
      h("span", { id: "status", class: "muted", onclick: () => syncNow() }),
      h("button", { class: "pill", onclick: signOut }, "Sign out")),
    h("input", { id: "search", type: "search", placeholder: "Search notes", value: query,
      oninput: (e) => { query = e.target.value; renderList(); } }),
    h("nav", { class: "views" }, [["notes", "Notes"], ["today", "Today"], ["archive", "Archive"]].map(([v, label]) =>
      h("button", { "data-view": v, onclick: () => { S.view = v; save(); renderList(); } }, label))),
    h("nav", { id: "chips", class: "chips" }),
    h("section", { id: "list" }),
    h("div", { class: "fab-wrap" },
      h("div", { id: "types", class: "types hidden" }, Object.entries(TYPES).map(([t, label]) =>
        h("button", { onclick: () => { $("#types").classList.add("hidden"); newCard(t); } }, label))),
      h("button", { class: "fab", "aria-label": "New card", onclick: () => $("#types").classList.toggle("hidden") }, "+")));
}

function render() {
  const root = $("#root");
  if (!config().url || !config().key) root.replaceChildren(setupScreen());
  else if (!S.session) root.replaceChildren(loginScreen());
  else { root.replaceChildren(mainScreen()); renderList(); }
}

// ------------------------------------------------------------------ start
render();
if (S.session) syncNow();
// Ask the browser to keep this app's storage (sign-in and notes) instead of clearing it under storage pressure.
if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
setInterval(() => { if (document.visibilityState === "visible" && !editing) syncNow(); }, 20000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") syncNow(); });
window.addEventListener("online", () => syncNow());
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js");
window.__sidebar = { state: () => S, syncNow };     // used by the automated tests
