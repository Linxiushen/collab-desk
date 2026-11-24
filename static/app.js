"use strict";

const byId = (id) => document.getElementById(id);
const app = { rooms: [], agents: [], active_model: "", model_status: "" };
let launchRoom = new URLSearchParams(location.search).get("room");
const ui = {
  room: null,
  detail: null,
  targets: new Set(),
  drafts: new Map(),
  sending: false,
  stateRequest: 0,
  detailRequest: 0,
  stateKey: "",
  streamKey: "",
  memberKey: "",
  nearBottom: true,
};

function make(tag, className, content) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (content !== undefined && content !== null)
    el.textContent = String(content);
  return el;
}

function button(className, content, handler) {
  const el = make("button", className, content);
  el.type = "button";
  if (handler) el.addEventListener("click", handler);
  return el;
}

async function api(path, body) {
  const options = { cache: "no-store", credentials: "same-origin" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    const fallback =
      response.status === 401
        ? "请重新运行启动器，在打开的窗口中进入工作区。"
        : "本地服务暂时不可用";
    throw new Error(typeof data?.error === "string" ? data.error : fallback);
  }
  return data;
}

const roomUrl = (id = ui.room) => "/api/rooms/" + encodeURIComponent(id);

function alertStatus(message = "") {
  byId("notice").textContent = message;
  byId("notice").hidden = !message;
  byId("networkLabel").textContent = message ? "连接异常" : "已连接本机服务";
  byId("netDot").className = message ? "offline" : "connected";
}

let toastTimer;
function toast(message) {
  const el = byId("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 2800);
}

function storedRoom() {
  try {
    return localStorage.getItem("tongzhuo.room");
  } catch {
    return null;
  }
}

function saveRoom(id) {
  try {
    localStorage.setItem("tongzhuo.room", id);
  } catch {
    /* storage may be disabled */
  }
}

const managedCodexId = "managed-local-codex";

function memberType(agent) {
  if (agent?.id === managedCodexId) return "本机";
  return agent?.kind === "external" ? "外部" : "内置";
}

function avatar(agent, human = false) {
  const tone = human
    ? "human"
    : agent?.id === "reviewer"
      ? "reviewer"
      : agent?.kind === "external"
        ? "external"
        : "analyst";
  const el = make("span", "avatar " + tone);
  el.append(make("i", "portrait"));
  el.setAttribute("aria-hidden", "true");
  return el;
}

const compactTeam = window.matchMedia("(max-width:1080px)");
const compactNav = window.matchMedia("(max-width:700px)");

function syncPanels() {
  const navOpen = compactNav.matches && byId("side").classList.contains("open");
  const teamOpen = compactTeam.matches
    ? byId("team").classList.contains("open")
    : !byId("layout").classList.contains("team-hidden");
  byId("veil").hidden = !(navOpen || (compactTeam.matches && teamOpen));
  byId("menu").setAttribute("aria-expanded", String(navOpen));
  byId("roomInfo").setAttribute("aria-expanded", String(teamOpen));
}

function setSidebar(open) {
  byId("side").classList.toggle("open", open);
  if (open) byId("team").classList.remove("open");
  syncPanels();
}

function setTeam(open) {
  if (compactTeam.matches) byId("team").classList.toggle("open", open);
  else byId("layout").classList.toggle("team-hidden", !open);
  if (open) byId("side").classList.remove("open");
  syncPanels();
}

function dismissOverlay() {
  byId("side").classList.remove("open");
  byId("team").classList.remove("open");
  syncPanels();
}

function navigation() {
  const query = byId("roomSearch").value.trim().toLocaleLowerCase();
  const key = JSON.stringify([app.rooms, app.agents, ui.room, query]);
  if (key === ui.stateKey) return;
  ui.stateKey = key;
  byId("roomCount").textContent = app.rooms.length;
  const roomItems = app.rooms
    .filter((room) => room.name.toLocaleLowerCase().includes(query))
    .map((room) => {
      const selected = room.id === ui.room;
      const row = button("nav-row" + (selected ? " active" : ""), "", () =>
        selectRoom(room.id),
      );
      if (selected) row.setAttribute("aria-current", "page");
      row.title = room.name;
      const title = make("span", "nav-copy");
      title.append(make("span", "ellipsis", room.name));
      if (room.last_message?.text) {
        title.append(
          make(
            "small",
            "ellipsis",
            (room.last_message.author || "") + " · " + room.last_message.text,
          ),
        );
      }
      row.append(make("span", "nav-chat"), title);
      return row;
    });
  if (!roomItems.length)
    roomItems.push(
      make("p", "no-matches", query ? "没有匹配的讨论室" : "还没有讨论室"),
    );
  byId("rooms").replaceChildren(...roomItems);
  const agents = app.agents.map((agent) => {
    const row = button("nav-row agent-row", "", () => showAgent(agent));
    row.title = agent.name;
    row.append(avatar(agent), make("span", "ellipsis", agent.name));
    return row;
  });
  byId("agents").replaceChildren(...agents);
}

function showModelStatus() {
  const name = app.active_model;
  const ready = app.model_status === "ready";
  const status = ready ? "模型已就绪" : "未连接模型";
  const label = name ? status + " · " + name : status;
  byId("model").textContent = label;
  byId("model").title = label;
  byId("teamModel").textContent = name || "未配置";
  byId("teamModelStatus").textContent = status;
  byId("teamModelStatus").className = ready ? "ready" : "unavailable";
  byId("model").parentNode.classList.toggle("ready", ready);
}

function rosterStatus(agent, state = {}) {
  if (state.working) return ["working", "正在回复"];
  if (state.pending) {
    if (agent.id === managedCodexId)
      return ["pending", "待回复 · " + state.pending];
    if (agent.kind === "external")
      return ["pending", "等待接单 · " + state.pending];
    return [
      "pending",
      app.model_status === "ready" ? "等待生成 · " + state.pending : "等待模型",
    ];
  }
  if (state.worker_status === "running") return ["listening", "自动接单已开启"];
  if (state.worker_status === "stopped") return ["paused", "接单进程已停止"];
  if (state.worker_status === "disabled") return ["paused", "自动接单已关闭"];
  const lastReply = parseDate(state.last_reply_at);
  if (lastReply) return ["previous", "上次回复 " + shortDate.format(lastReply)];
  return [];
}

function renderRoster(detail) {
  const activity = detail.member_activity || {};
  const key = JSON.stringify([detail.members, activity, app.model_status]);
  if (ui.rosterKey === key) return;
  ui.rosterKey = key;
  byId("teamCount").textContent = String(detail.members.length + 1);
  byId("headerAvatars").replaceChildren(
    ...detail.members.slice(0, 3).map((a) => avatar(a)),
  );
  const human = make("div", "roster-human");
  const personName = make("div");
  personName.append(make("b", "", "我"), make("small", "", "工作区所有者"));
  human.append(avatar(null, true), personName);
  const cards = detail.members.map((agent) => {
    const card = button("member-card", "", () => showAgent(agent));
    const info = make("div", "member-info");
    const line = make("div", "member-line");
    line.append(
      make("b", "", agent.name),
      make(
        "span",
        "type " + (memberType(agent) === "外部" ? "ext" : ""),
        memberType(agent),
      ),
    );
    info.append(line);
    if (agent.id === managedCodexId)
      info.append(make("small", "mode-label", "本机 Qwen · 文字回复"));
    info.append(make("p", "", agent.role || "尚未设置职责。"));
    const [kind, label] = rosterStatus(agent, activity[agent.id]);
    if (label) info.append(make("small", "status-tag " + kind, label));
    card.append(avatar(agent), info);
    return card;
  });
  byId("roster").replaceChildren(human, ...cards);
}

async function refreshState() {
  const serial = ++ui.stateRequest;
  try {
    const data = await api("/api/state");
    if (serial !== ui.stateRequest) return;
    Object.assign(app, data);
    showModelStatus();
    navigation();
    if (!app.rooms.some((room) => room.id === ui.room)) {
      const preferred = storedRoom();
      const initial =
        app.rooms.find((room) => room.id === launchRoom) ||
        app.rooms.find((room) => room.id === preferred) ||
        app.rooms[0];
      if (launchRoom !== null && app.rooms.length) {
        launchRoom = null;
        const url = new URL(location.href);
        url.searchParams.delete("room");
        history.replaceState(null, "", url);
      }
      if (initial) await selectRoom(initial.id);
      else {
        byId("roomMeta").textContent = "当前没有讨论室";
        byId("thread").replaceChildren(
          make("p", "event", "点击「新建讨论室」开始。"),
        );
        alertStatus();
      }
    }
  } catch (err) {
    if (serial === ui.stateRequest)
      alertStatus(err.message || "无法连接本地服务");
  }
}

function closeTargets() {
  byId("targetMenu").hidden = true;
  byId("targets").setAttribute("aria-expanded", "false");
}

async function selectRoom(id) {
  dismissOverlay();
  if (ui.room === id && ui.detail) return;
  if (ui.room) ui.drafts.set(ui.room, byId("draft").value);
  ui.room = id;
  ui.detail = null;
  ui.targets.clear();
  ui.streamKey = "";
  ui.memberKey = "";
  ui.rosterKey = "";
  ui.nearBottom = true;
  closeTargets();
  saveRoom(id);
  byId("draft").value = ui.drafts.get(id) || "";
  byId("thread").replaceChildren();
  byId("title").textContent =
    app.rooms.find((room) => room.id === id)?.name || "讨论室";
  byId("roomMeta").textContent = "正在读取…";
  byId("roster").replaceChildren();
  byId("teamCount").textContent = "";
  byId("headerAvatars").replaceChildren();
  byId("activity").textContent = "";
  byId("targetName").textContent = "全体成员";
  controls();
  navigation();
  await refreshRoom();
}

function parseDate(value) {
  if (value === null || value === undefined) return null;
  let timestamp = value;
  if (typeof timestamp === "number" && timestamp < 1e12) timestamp *= 1000;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? null : date;
}

const dayFormat = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "long",
  day: "numeric",
});
const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const shortDate = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
function formattedInline(text) {
  const body = document.createDocumentFragment();
  const parts =
    /\[([^\[\]\n]{1,500})\]\((https?:\/\/(?:[^\s<>"'()]|\([^\s<>"'()]*\))+)\)|(https?:\/\/(?:[^\s<>"'()，。；！？、：（）【】*]|\([^\s<>"'()]*\))+)|\*\*([^*\n]+)\*\*/giu;
  let end = 0;
  for (const match of text.matchAll(parts)) {
    body.append(text.slice(end, match.index));
    end = match.index + match[0].length;
    const [, label, markdownUrl, bareUrl, bold] = match;
    if (bold) {
      body.append(make("strong", "", bold));
      continue;
    }
    const urlText = markdownUrl || bareUrl.replace(/[.,;:!?]+$/u, "");
    try {
      const url = new URL(urlText);
      if (!["http:", "https:"].includes(url.protocol) || !url.hostname)
        throw new Error("Invalid URL");
      const link = make("a", "", label || urlText);
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = url.href;
      body.append(link);
      if (bareUrl) body.append(bareUrl.slice(urlText.length));
    } catch {
      body.append(match[0]);
    }
  }
  body.append(text.slice(end));
  return body;
}

function formattedBody(text) {
  const body = make("div", "bubble");
  const fence = String.fromCharCode(96).repeat(3);
  const pattern = new RegExp(
    "^" +
      fence +
      "([^\\n]*)\\n([\\s\\S]*?)(?:^" +
      fence +
      "[ \\t]*(?=\\n|$)|$(?![\\s\\S]))",
    "gm",
  );
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    body.append(formattedInline(text.slice(end, match.index)));
    const block = make("div", "code-block");
    block.append(
      make("div", "code-label", match[1].trim().slice(0, 32) || "代码"),
    );
    const pre = make("pre");
    pre.append(make("code", "", match[2].replace(/\n$/, "")));
    block.append(pre);
    body.append(block);
    end = match.index + match[0].length;
  }
  body.append(formattedInline(text.slice(end)));
  return body;
}

async function copyMessage(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制消息");
  } catch {
    showModal("复制消息", codeRow(text));
  }
}

function renderMessage(msg, original) {
  if (msg.kind === "system" || msg.kind === "error") {
    const event = make("div", "event" + (msg.kind === "error" ? " error" : ""));
    if (msg.kind === "error")
      event.append(
        make("b", "", "回复失败" + (msg.author ? " · " + msg.author : "")),
      );
    event.append(document.createTextNode(msg.text));
    return event;
  }
  const human = msg.kind === "human";
  const name = msg.author || (human ? "我" : "Agent");
  const row = make("article", "message" + (human ? " own" : ""));
  const agent = app.agents.find((a) => a.id === msg.agent_id) || { name };
  const body = make("div", "message-body");
  const meta = make("div", "message-meta");
  const date = parseDate(msg.created_at);
  meta.append(make("b", "", human ? "我" : name));
  if (!human && agent.kind)
    meta.append(make("span", "message-type", memberType(agent) + " Agent"));
  if (date) {
    const time = make("time", "", timeFormat.format(date));
    time.dateTime = date.toISOString();
    meta.append(time);
  }
  body.append(meta);
  if (!human && original) {
    const quote = button("quoted", "↳ " + original.text, () => {
      const target = document.getElementById("message-" + original.id);
      target?.scrollIntoView({ block: "center" });
      target?.focus({ preventScroll: true });
    });
    quote.title = "定位到提问";
    body.append(quote);
  }
  body.append(formattedBody(msg.text));
  const copy = button("copy-message", "复制", () => copyMessage(msg.text));
  copy.setAttribute("aria-label", "复制这条消息");
  body.append(copy);
  row.id = "message-" + msg.id;
  row.tabIndex = -1;
  if (!human) row.append(avatar(agent));
  row.append(body);
  return row;
}

function blankRoom(members) {
  const empty = make("div", "empty");
  const art = make("div", "illustration");
  art.setAttribute("aria-hidden", "true");
  art.append(
    make("i", "orbit"),
    make("i", "orb-dot"),
    avatar({ id: "analyst" }),
    avatar({ id: "reviewer" }),
    make("i", "speech", "···"),
  );
  empty.append(art, make("h2", "", "今天，一起讨论什么？"));
  const explanation = members.length
    ? "把问题交给桌上的 Agent，也可以 @ 指定一位成员回应。"
    : "你可以先记下想法，再通过「管理成员」邀请 Agent 加入。";
  empty.append(make("p", "", explanation));
  const suggestions = make("div", "suggestions");
  const prompts = [
    ["一起评估一个想法", "我想评估这个想法："],
    ["检查方案的风险", "请帮我检查下面这个方案的风险，并给出改进建议："],
  ];
  for (const [title, prompt] of prompts) {
    const item = button("suggestion", title, () => {
      const draft = byId("draft");
      draft.value = draft.value.trim() ? draft.value + "\n" + prompt : prompt;
      draft.focus();
      controls();
    });
    item.append(make("span", "", "↗"));
    suggestions.append(item);
  }
  empty.append(suggestions);
  return empty;
}

function renderThread(detail) {
  const key = JSON.stringify([detail.messages, detail.members.length]);
  if (key === ui.streamKey) return;
  const wasNearBottom = ui.nearBottom;
  ui.streamKey = key;
  const content = document.createDocumentFragment();
  let lastDay = "";
  const questions = new Map();
  for (const msg of detail.messages) {
    const date = parseDate(msg.created_at);
    const day = date ? dayFormat.format(date) : "";
    if (day && day !== lastDay) content.append(make("div", "day", day));
    lastDay = day;
    if (msg.kind === "human") questions.set(msg.id, msg);
    content.append(renderMessage(msg, questions.get(msg.root)));
  }
  if (!detail.messages.length) content.append(blankRoom(detail.members));
  const stream = byId("stream");
  const previous = stream.scrollTop;
  byId("thread").replaceChildren(content);
  stream.scrollTop = wasNearBottom ? stream.scrollHeight : previous;
}

function targetPicker() {
  const picker = byId("targetMenu");
  const members = ui.detail?.members || [];
  const chosen = members.filter((a) => ui.targets.has(a.id));
  byId("targetName").textContent = chosen.length
    ? chosen.map((a) => a.name).join("、")
    : "全体成员";
  const rows = [];
  for (const agent of [null, ...members]) {
    const row = make("label");
    const check = make("input");
    check.type = "checkbox";
    check.checked = agent ? ui.targets.has(agent.id) : !chosen.length;
    row.append(check, document.createTextNode(agent ? agent.name : "全体成员"));
    check.addEventListener("change", () => {
      if (!agent) ui.targets.clear();
      else if (check.checked) ui.targets.add(agent.id);
      else ui.targets.delete(agent.id);
      targetPicker();
      const index = agent
        ? members.findIndex((member) => member.id === agent.id) + 1
        : 0;
      picker.querySelectorAll("input")[index]?.focus();
    });
    rows.push(row);
  }
  picker.replaceChildren(...rows);
}

function controls() {
  const canWrite = !!ui.detail;
  byId("draft").disabled = !canWrite;
  byId("targets").disabled = !canWrite || !ui.detail.members.length;
  byId("members").disabled = !canWrite;
  byId("send").disabled =
    !canWrite || ui.sending || !byId("draft").value.trim();
}

async function refreshRoom() {
  if (!ui.room) return;
  const room = ui.room;
  const serial = ++ui.detailRequest;
  try {
    const detail = await api(roomUrl(room));
    if (room !== ui.room || serial !== ui.detailRequest) return;
    ui.detail = detail;
    byId("title").textContent = detail.room.name;
    document.title = detail.room.name + " · 协作台";
    byId("roomMeta").textContent = detail.members.length + " 位 Agent";
    renderRoster(detail);
    const activity = [];
    if (detail.busy) {
      const statuses = Object.values(detail.member_activity || {});
      const working = statuses.some((item) => item.working > 0);
      activity.push(
        working || !statuses.length
          ? "内置 Agent 正在整理回复…"
          : app.model_status === "ready"
            ? "回复任务正在排队"
            : "任务已保存，等待模型就绪",
      );
    }
    const managed = detail.member_activity?.[managedCodexId];
    const managedPending = (managed?.pending || 0) + (managed?.working || 0);
    if (managedPending)
      activity.push("等待本机 Codex 回应（" + managedPending + "）");
    const manualExternal = detail.waiting_external - managedPending;
    if (manualExternal > 0)
      activity.push("等待外部 Agent 回应（" + manualExternal + "）");
    byId("activity").textContent = activity.join(" · ");
    const memberKey = JSON.stringify(detail.members);
    if (ui.memberKey !== memberKey) {
      ui.memberKey = memberKey;
      const available = new Set(detail.members.map((a) => a.id));
      ui.targets = new Set([...ui.targets].filter((id) => available.has(id)));
      targetPicker();
    }
    renderThread(detail);
    controls();
    alertStatus();
  } catch (err) {
    if (room === ui.room && serial === ui.detailRequest)
      alertStatus(err.message || "无法读取讨论记录");
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (!ui.detail || ui.sending) return;
  const input = byId("draft");
  const original = input.value;
  const text = original.trim();
  if (!text) return;
  const room = ui.room;
  const body = { text };
  if (ui.targets.size) body.targets = [...ui.targets];
  ui.sending = true;
  closeTargets();
  controls();
  try {
    await api(roomUrl(room) + "/messages", body);
    if (ui.room === room) {
      if (input.value === original) input.value = "";
      ui.drafts.set(room, input.value);
      ui.nearBottom = true;
      await refreshRoom();
      input.focus();
    } else if (ui.drafts.get(room) === original) {
      ui.drafts.delete(room);
    }
  } catch (err) {
    toast("发送失败：" + err.message);
  } finally {
    ui.sending = false;
    controls();
  }
}

function showModal(title, ...children) {
  const dialog = byId("dialog");
  closeTargets();
  dismissOverlay();
  byId("dialogTitle").textContent = title;
  byId("dialogBody").replaceChildren(...children);
  if (!dialog.open) dialog.showModal();
}

function field(title, multiline = false) {
  const label = make("label", "field");
  const input = make(multiline ? "textarea" : "input");
  if (multiline) input.rows = 3;
  label.append(document.createTextNode(title), input);
  return { label, input };
}

function choices(selected, title = "选择 Agent") {
  const box = make("fieldset", "member-list");
  box.append(make("legend", "", title));
  const checked = [];
  for (const agent of app.agents) {
    const label = make("label", "member-option");
    const input = make("input");
    input.type = "checkbox";
    input.checked = selected.has(agent.id);
    input.value = agent.id;
    const desc = make("div");
    desc.append(
      make("b", "", agent.name),
      make("small", "", agent.role || memberType(agent) + " Agent"),
    );
    label.append(input, avatar(agent), desc);
    checked.push(input);
    box.append(label);
  }
  return {
    box,
    ids: () => checked.filter((el) => el.checked).map((el) => el.value),
  };
}

function formModal(title, items, action, onSubmit) {
  const form = make("form", "modal-form");
  const error = make("div", "form-error");
  error.setAttribute("role", "alert");
  const actions = make("div", "modal-actions");
  const close = button("secondary", "取消", () => byId("dialog").close());
  const save = make("button", "primary", action);
  save.type = "submit";
  actions.append(close, save);
  form.append(...items, error, actions);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (save.disabled) return;
    save.disabled = true;
    error.textContent = "";
    try {
      await onSubmit();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      save.disabled = false;
    }
  });
  showModal(title, form);
}

function createRoom() {
  const name = field("讨论室名称");
  name.input.required = true;
  name.input.maxLength = 60;
  name.input.placeholder = "例如：新产品评审";
  const selected = choices(
    new Set(app.agents.filter((a) => a.kind !== "external").map((a) => a.id)),
    "邀请成员",
  );
  formModal("新建讨论室", [name.label, selected.box], "创建", async () => {
    const clean = name.input.value.trim();
    if (!clean) throw new Error("请给讨论室起个名字。");
    const result = await api("/api/rooms", {
      name: clean,
      agent_ids: selected.ids(),
    });
    byId("dialog").close();
    await refreshState();
    await selectRoom(result.id);
  });
  name.input.focus();
}

function editMembers() {
  if (!ui.detail) return;
  const id = ui.room;
  const selected = choices(
    new Set(ui.detail.members.map((a) => a.id)),
    "本讨论室的 Agent",
  );
  formModal("管理成员", [selected.box], "保存成员", async () => {
    await api(roomUrl(id) + "/members", { agent_ids: selected.ids() });
    byId("dialog").close();
    await refreshRoom();
  });
}

function codeRow(value) {
  const row = make("div", "code-row");
  const code = make("input", "token");
  code.readOnly = true;
  code.value = value;
  code.setAttribute("aria-label", "可复制内容");
  const copy = button("secondary", "复制", async () => {
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = "已复制";
    } catch {
      code.focus();
      code.select();
      copy.textContent = "请手动复制";
    }
  });
  row.append(code, copy);
  return row;
}

function externalInfo(id, token) {
  const box = make("div");
  if (token) {
    box.append(
      make("p", "help", "凭证只显示这一次。请复制后在登录命令的提示中粘贴。"),
      codeRow(token),
    );
  }
  box.append(
    make("p", "help", "在本项目目录运行以下命令，登录后即可领取任务。"),
  );
  box.append(codeRow("python3 agent_cli.py login --profile " + id));
  box.append(codeRow("python3 agent_cli.py inbox --profile " + id));
  box.append(
    make("p", "help", "领取后，替换任务编号和回复内容，再发回讨论室。"),
  );
  box.append(
    codeRow(
      "python3 agent_cli.py reply JOB_ID --profile " +
        id +
        " --text '回复内容'",
    ),
  );
  box.append(
    make(
      "p",
      "help",
      "在讨论室的「管理成员」中勾选这个 Agent，即可指名让它回应。",
    ),
  );
  box.append(
    make(
      "p",
      "help",
      "登录后可手动运行 Codex 处理一个任务。持续接单时在命令末尾加上 --watch。",
    ),
  );
  box.append(codeRow("python3 codex_worker.py --profile " + id));
  return box;
}

function createAgent() {
  const name = field("成员名称");
  name.input.required = true;
  name.input.maxLength = 36;
  name.input.placeholder = "例如：我的调研助手";
  const role = field("职责说明", true);
  role.input.maxLength = 400;
  role.input.placeholder = "这个 Agent 擅长做什么？";
  formModal(
    "接入外部 Agent",
    [name.label, role.label],
    "创建接入凭证",
    async () => {
      const clean = name.input.value.trim();
      if (!clean) throw new Error("请输入成员名称。");
      const data = await api("/api/agents", {
        name: clean,
        role: role.input.value.trim(),
      });
      showModal("接入「" + clean + "」", externalInfo(data.id, data.token));
      await refreshState();
    },
  );
  name.input.focus();
}

function showAgent(agent) {
  const info = make("div");
  info.append(
    make(
      "p",
      "profile-type",
      agent.id === managedCodexId
        ? "本机 Codex · 本机 Qwen · 文字回复"
        : memberType(agent) + " Agent",
    ),
  );
  info.append(make("p", "help", agent.role || "尚未填写职责说明。"));
  if (agent.id === managedCodexId) {
    info.append(
      make(
        "p",
        "help",
        "工作区负责启动这个成员。用「打开协作台Codex群.command」进入专属讨论室，即可向它提问。",
      ),
    );
  } else if (agent.kind === "external") info.append(externalInfo(agent.id));
  else {
    info.append(
      make(
        "p",
        "help",
        "内置角色通过当前工作区配置的模型回复。你可以在讨论室中 @ 它。",
      ),
    );
  }
  showModal(agent.name, info);
}

byId("newRoom").addEventListener("click", createRoom);
byId("members").addEventListener("click", editMembers);
byId("addAgent").addEventListener("click", createAgent);
byId("closeDialog").addEventListener("click", () => byId("dialog").close());
byId("dialog").addEventListener("close", () => {
  // Drop the one-time enrollment token when the modal is dismissed.
  if (!byId("dialog").open) byId("dialogBody").replaceChildren();
});
byId("menu").addEventListener("click", () => setSidebar(true));
byId("veil").addEventListener("click", dismissOverlay);
byId("roomInfo").addEventListener("click", () =>
  setTeam(byId("roomInfo").getAttribute("aria-expanded") !== "true"),
);
byId("closeTeam").addEventListener("click", () => setTeam(false));
byId("roomSearch").addEventListener("input", navigation);
compactTeam.addEventListener("change", dismissOverlay);
compactNav.addEventListener("change", dismissOverlay);
syncPanels();
byId("composer").addEventListener("submit", sendMessage);
byId("draft").addEventListener("input", controls);
byId("draft").addEventListener("keydown", (event) => {
  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.isComposing &&
    event.keyCode !== 229
  ) {
    event.preventDefault();
    byId("composer").requestSubmit();
  }
});
byId("targets").addEventListener("click", () => {
  const picker = byId("targetMenu");
  picker.hidden = !picker.hidden;
  byId("targets").setAttribute("aria-expanded", String(!picker.hidden));
  if (!picker.hidden) picker.querySelector("input")?.focus();
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".target-wrap")) closeTargets();
});
document.addEventListener("keydown", (event) => {
  if (
    (event.metaKey || event.ctrlKey) &&
    event.key.toLowerCase() === "k" &&
    !byId("dialog").open
  ) {
    event.preventDefault();
    if (compactNav.matches) setSidebar(true);
    byId("roomSearch").focus();
    byId("roomSearch").select();
  }
  if (event.key === "Escape") {
    if (!byId("targetMenu").hidden) {
      closeTargets();
      byId("targets").focus();
    }
    dismissOverlay();
  }
});
byId("stream").addEventListener("scroll", () => {
  const el = byId("stream");
  ui.nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 72;
});
let ticks = 0;
async function poll() {
  if (!document.hidden) {
    if (ticks % 5 === 0 || !ui.room) await refreshState();
    if (ui.room) await refreshRoom();
    ticks++;
  }
  setTimeout(poll, 1800);
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshState().then(refreshRoom);
});
poll();
