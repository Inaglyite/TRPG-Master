// TRPG Agent 前端 —— WebSocket 通信 + UI 渲染

// 计算后端地址。Electron 桌面端用 file:// 加载页面，location.hostname 为空，
// 此时回退到 localhost；浏览器端则用当前 hostname（支持远程访问）。
const WS_HOST = location.hostname || "localhost";
const WS_URL = `ws://${WS_HOST}:8765/ws`;

// ---- DOM 引用 ----
const messagesEl = document.getElementById("messages")!;
const optionsBar = document.getElementById("options-bar")!;
const userInput = document.getElementById("user-input") as HTMLInputElement;
const btnSend = document.getElementById("btn-send") as HTMLButtonElement;
const btnSave = document.getElementById("btn-save")!;
const btnLoad = document.getElementById("btn-load")!;
const btnNew = document.getElementById("btn-new")!;
const btnPanel = document.getElementById("btn-panel")!;
const charPanel = document.getElementById("char-panel")!;
const connStatus = document.getElementById("conn-status")!;

function setConn(state: "connecting" | "connected" | "disconnected") {
  connStatus.className = state;
  connStatus.title =
    state === "connected" ? "已连接到守秘人" :
    state === "connecting" ? "连接中…" : "连接已断开，正在重试";
}
const savePanelOverlay = document.getElementById("save-panel-overlay")!;
const savePanelClose = document.getElementById("save-panel-close")!;
const savePanelNew = document.getElementById("save-panel-new")!;
const savePanelList = document.getElementById("save-panel-list")!;

const modalOverlay = document.getElementById("modal-overlay")!;
const modalText = document.getElementById("modal-text")!;
const modalYes = document.getElementById("modal-yes")!;
const modalNo = document.getElementById("modal-no")!;
const startOverlay = document.getElementById("start-overlay")!;
const btnStart = document.getElementById("btn-start") as HTMLButtonElement;
const btnContinue = document.getElementById("btn-continue") as HTMLButtonElement;

// ---- WebSocket ----
let ws: WebSocket;
let streamTarget: HTMLElement | null = null;  // 当前流式输出的目标元素
let msgIdCounter = 0;

// 待发送队列：ws 处于 CONNECTING 时先缓存，OPEN 后一并发出，避免 InvalidStateError
const sendQueue: string[] = [];

/** 安全发送：ws 未就绪时入队，就绪时直接发 */
function safeSend(payload: string) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(payload);
  } else {
    sendQueue.push(payload);
  }
}

function connect() {
  setConn("connecting");
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    setConn("connected");
    addMsg("system", "已连接到守秘人……");
    // 首次连接自动开始新游戏
    ws.send(JSON.stringify({ type: "ping" }));
    // 排空连接前积压的消息
    while (sendQueue.length) ws.send(sendQueue.shift()!);
    // 连接建立后请求角色状态
    ws.send(JSON.stringify({ type: "state" }));
  };
  ws.onmessage = handleMessage;
  ws.onclose = () => {
    setConn("disconnected");
    addMsg("error", "连接断开。5秒后重试……");
    setTimeout(connect, 5000);
  };
  ws.onerror = () => setConn("disconnected");
}

function handleMessage(e: MessageEvent) {
  const data = JSON.parse(e.data);
  switch (data.type) {
    case "pong": break;
    case "gm_turn_start": onGmTurnStart(); break;
    case "narrative_chunk": onNarrativeChunk(data.text); break;
    case "tension": onTension(data.text); break;
    case "dice_result": onDice(data.summary); break;
    case "glm_summary": onSummary(data.text); break;
    case "suggest_check": onSuggest(data); break;
    case "done": onDone(); break;
    case "error": addMsg("error", data.message); if (gameStarting) resetStartButton(); break;
    case "saved":
      addMsg("system", data.ok ? `存档成功 (${data.slot_id})。` : "存档失败。");
      if (!savePanelOverlay.classList.contains("hidden")) {
        safeSend(JSON.stringify({ type: "save_list" }));
      }
      break;
    case "save_deleted":
      addMsg("system", `已删除存档 ${data.slot_id}。`);
      if (!savePanelOverlay.classList.contains("hidden")) {
        safeSend(JSON.stringify({ type: "save_list" }));
      }
      break;
    case "quit_ok": addMsg("system", "进度已保存。"); disconnectCleanly(); break;
    case "game_over": showEnding(data); break;
    case "save_list":
      onSaveList(data);
      if (!savePanelOverlay.classList.contains("hidden")) {
        renderSavePanel(data.saves || []);
      }
      break;
    case "save_available": onSaveAvailable(data); break;  // 兼容旧版
    case "loaded": addMsg("system", data.ok ? `读档成功，恢复了 ${data.count} 条消息。` : "未找到存档。"); break;
    case "state_data": updateCharPanel(data.data); updateCluePanel(data.clues); break;
  }
}

// ---- 开场 / GM 回合状态 ----
let gameStarted = false;   // 游戏是否已开始（隐藏开场遮罩用）
let gameStarting = false;  // 开场回合是否进行中（防重复点击）

function onGmTurnStart() {
  // 服务端开始跑 GM 回合：隐藏开场遮罩，进入"等待叙述"状态
  if (!gameStarted) {
    gameStarted = true;
    startOverlay.classList.add("hidden");
  }
  // GM 回合进行中：禁用输入，提示玩家等待，显示思考动画
  enableInput(false);
  showGmThinking();
}

/** 显示"守秘人思考中"加载指示，直到第一条叙述文本或 done 到达 */
function showGmThinking() {
  removeLoading();
  const dots = document.createElement("div");
  dots.className = "msg system";
  dots.id = "loading-dots";
  dots.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div><span style="margin-left:8px;font-size:13px">守秘人正在叙述……</span>';
  messagesEl.appendChild(dots);
  scrollDown();
}

function resetStartButton() {
  gameStarting = false;
  btnStart.disabled = false;
  btnContinue.disabled = false;
  btnStart.textContent = btnContinue.classList.contains("hidden") ? "🕯 点燃烛火，开始故事" : "🕯 开始新游戏";
  btnContinue.textContent = "📜 继续游戏";
}

function startGame() {
  if (gameStarting) return;
  gameStarting = true;
  btnStart.disabled = true;
  btnContinue.disabled = true;
  btnStart.textContent = "守秘人正在布景……";
  safeSend(JSON.stringify({ type: "start" }));
}

function continueGame() {
  // 打开存档面板让玩家选——不直接加载最新档
  openSavePanel();
}

function onSaveAvailable(data: any) {
  if (data.has_save) {
    btnContinue.classList.remove("hidden");
    btnStart.textContent = "🕯 开始新游戏";
  }
}

function onSaveList(data: any) {
  const saves = data.saves || [];
  const hint = document.getElementById("start-hint")!;

  // 开始界面：只显示最近存档摘要 + 继续游戏按钮
  if (saves.length > 0) {
    btnContinue.classList.remove("hidden");
    btnStart.textContent = "🕯 开始新游戏";
    const latest = saves[0];
    hint.textContent =
      `最近存档: ${latest.scene_name || "?"} | HP ${latest.hp || "?"} SAN ${latest.san || "?"} | ${latest.clue_count || 0} 条线索`;
  } else {
    hint.textContent = "还未有存档。开始游戏后进度将自动保存。";
  }

  // 游戏内的存档管理面板（💾按钮）始终同步
  renderSavePanel(saves);
}

// ---- 消息渲染 ----
function addMsg(kind: string, text: string): HTMLElement {
  const el = document.createElement("div");
  el.className = `msg ${kind}`;
  el.id = `msg-${++msgIdCounter}`;
  el.innerHTML = text.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
                     .replace(/\*(.+?)\*/g, "<i>$1</i>")
                     .replace(/---/g, "<hr>")
                     .replace(/\n/g, "<br>");
  messagesEl.appendChild(el);
  scrollDown();
  return el;
}

function scrollDown() {
  setTimeout(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }, 50);
}

// ---- 流式文本 ----
function onNarrativeChunk(text: string) {
  // 第一条叙述文本到达时，移除"守秘人思考中"指示
  removeLoading();
  if (!streamTarget || streamTarget.className !== "msg gm streaming-cursor") {
    streamTarget = addMsg("gm", "");
    streamTarget.classList.add("streaming-cursor");
  }
  // 追加文本（处理 markdown 粗体）
  streamTarget.innerHTML += text
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\*(.+?)\*/g, "<i>$1</i>")
    .replace(/\n/g, "<br>");
  scrollDown();
}

// ---- 事件处理 ----
function onTension(text: string) {
  addMsg("tension", text);
  showGmThinking();
}

function onDice(text: string) {
  removeLoading();
  // 结束当前流式气泡，骰子后的叙事另开新气泡
  if (streamTarget) {
    streamTarget.classList.remove("streaming-cursor");
    streamTarget = null;
  }
  addMsg("dice", text);
}

function onSummary(text: string) {
  addMsg("summary", text);
}

function onSuggest(data: any) {
  removeLoading();
  modalText.innerHTML = `
    <div style="font-size:13px;color:var(--text-dim)">${data.description}</div>
    <div style="margin-top:8px;">
      <b>${data.skill}</b>（${data.attribute}）
      — 难度：${data.dc_label}（DC ${data.dc}）
    </div>
  `;
  modalOverlay.classList.remove("hidden");
  modalYes.focus();
}

function onDone() {
  removeLoading();
  // 结束流式光标
  if (streamTarget) {
    streamTarget.classList.remove("streaming-cursor");
    streamTarget = null;
  }
  // 解析选项：从最近的 GM 消息中提取（跳过骰子、摘要等非叙事消息）
  const opts: { label: string; isFree: boolean }[] = [];
  const children = messagesEl.children;
  for (let i = children.length - 1; i >= 0; i--) {
    const el = children[i];
    if (!el.classList.contains("gm")) continue;
    const text = el.innerHTML;
    const lines = text.split("<br>");

    // 只在"你可以"之后解析选项——避免叙事正文中带编号的内容被误判
    let startIdx = 0;
    for (let j = lines.length - 1; j >= 0; j--) {
      if (/你可以|您可以/.test(lines[j])) {
        startIdx = j;
        break;
      }
    }

    for (let j = startIdx; j < lines.length; j++) {
      const m = lines[j].match(/^\d+\.\s*(.+)/);
      if (m) {
        const label = m[1].replace(/<[^>]+>/g, "");
        const isFree = label.includes("自由行动") || label.includes("你决定做什么");
        opts.push({ label, isFree });
      }
    }
    break;  // 只查最近一条 GM 消息
  }

  if (opts.length > 0) {
    renderOptions(opts);
  } else {
    // 没解析出选项，显示输入框
    optionsBar.innerHTML = "";
    enableInput(true);
  }
}

function removeLoading() {
  const dots = document.getElementById("loading-dots");
  if (dots) dots.remove();
}

// ---- 选项按钮 ----
function renderOptions(opts: { label: string; isFree: boolean }[]) {
  optionsBar.innerHTML = "";
  opts.forEach((o, i) => {
    const btn = document.createElement("button");
    btn.className = "opt-btn" + (o.isFree ? " free" : "");
    btn.textContent = `${i + 1}. ${o.label}`;
    btn.onclick = () => {
      sendAction(o.label);
    };
    optionsBar.appendChild(btn);
  });
  // 自由行动时同时启用输入框
  const hasFree = opts.some(o => o.isFree);
  enableInput(hasFree);
}

function enableInput(on: boolean) {
  userInput.disabled = !on;
  btnSend.disabled = !on;
  userInput.placeholder = on ? "你决定做什么？" : "守秘人正在叙述……";
  if (on) userInput.focus();
}

// ---- 发送 ----
function sendAction(text: string) {
  addMsg("player", text);
  optionsBar.innerHTML = "";
  enableInput(false);
  showGmThinking();
  safeSend(JSON.stringify({ type: "action", content: text }));
}

function sendSuggestReply(confirmed: boolean) {
  modalOverlay.classList.add("hidden");
  if (confirmed) {
    addMsg("player", "🎲 确定尝试！");
  } else {
    addMsg("player", "↩ 放弃行动。");
  }
  safeSend(JSON.stringify({ type: "suggest_reply", confirmed }));
}

// ---- 角色面板 ----
function updateCharPanel(raw: string) {
  try {
    const data = JSON.parse(raw);
    document.getElementById("hp-bar")!.textContent = `${data.hp} / ${data.max_hp}`;
    document.getElementById("san-bar")!.textContent = `${data.san} / ${data.max_san}`;
    const hpPct = (data.hp / data.max_hp) * 100;
    const sanPct = (data.san / data.max_san) * 100;

    // HP bar
    let hpBar = document.getElementById("hp-fill");
    if (!hpBar) {
      const bar = document.createElement("div"); bar.className = "stat-bar";
      const fill = document.createElement("div"); fill.className = "stat-bar-fill hp";
      fill.id = "hp-fill"; fill.style.width = `${hpPct}%`;
      bar.appendChild(fill);
      document.getElementById("hp-bar")!.after(bar);
    } else {
      hpBar.style.width = `${hpPct}%`;
    }

    let sanBar = document.getElementById("san-fill");
    if (!sanBar) {
      const bar = document.createElement("div"); bar.className = "stat-bar";
      const fill = document.createElement("div"); fill.className = "stat-bar-fill san";
      fill.id = "san-fill"; fill.style.width = `${sanPct}%`;
      bar.appendChild(fill);
      document.getElementById("san-bar")!.after(bar);
    } else {
      sanBar.style.width = `${sanPct}%`;
    }

    const attrs = data.attributes || {};
    const attrList = document.getElementById("attr-list")!;
    attrList.innerHTML = Object.entries(attrs)
      .map(([k, v]) => `<div class="stat-row"><span>${k}</span><span>${v}</span></div>`)
      .join("");

    const inv = document.getElementById("inv-list")!;
    inv.innerHTML = (data.inventory || []).map((s: string) => `<div>• ${s}</div>`).join("");
  } catch {}
}

function updateCluePanel(cluesRaw: string) {
  try {
    const clues = JSON.parse(cluesRaw);
    const names: Record<string, string> = {
      investigation: "🔍 探案线索",
      event: "📜 事件线索",
      task: "🎯 任务线索",
      npc: "👤 人物线索",
    };
    const cluesList = document.getElementById("clues-list")!;
    let html = "";
    if (typeof clues === "object" && !Array.isArray(clues)) {
      for (const cat of ["investigation", "event", "task", "npc"]) {
        const items = clues[cat] || [];
        if (items.length > 0) {
          html += `<div class="clue-cat">${names[cat] || cat}</div>`;
          for (const item of items) {
            html += `<div class="clue-item">• ${item.text}</div>`;
          }
        }
      }
    }
    if (!html) {
      html = '<div class="clue-item" style="color:var(--text-faint)">暂无记录</div>';
    }
    cluesList.innerHTML = html;
  } catch {}
}

function loadState() {
  safeSend(JSON.stringify({ type: "state" }));
}

function showEnding(data: any) {
  removeLoading();
  const emoji = data.ending_type === "good" ? "🏆" : data.ending_type === "bad" ? "💀" : "🌫";
  // 显示结局提议按钮
  const btnConfirm = document.createElement("button");
  btnConfirm.className = "opt-btn";
  btnConfirm.style.cssText = "flex:2;background:var(--gold);color:var(--bg);font-weight:bold";
  btnConfirm.textContent = emoji + " 确认结束 —— " + data.title;
  btnConfirm.id = "btn-end-confirm";

  const btnContinue = document.createElement("button");
  btnContinue.className = "opt-btn";
  btnContinue.textContent = "🔄 继续探索";
  btnContinue.id = "btn-end-continue";

  optionsBar.innerHTML = "";
  optionsBar.appendChild(btnConfirm);
  optionsBar.appendChild(btnContinue);

  btnConfirm.onclick = () => {
    const html = [
      '<div class="ending-box">',
      '<div class="ending-emoji">' + emoji + '</div>',
      '<div class="ending-title">' + data.title + '</div>',
      '<div class="ending-summary">' + data.summary + '</div>',
      '</div>'
    ].join("");
    const el = addMsg("gm", html);
    el.classList.add("ending");
    optionsBar.innerHTML = "";
    enableInput(false);
  };

  btnContinue.onclick = () => {
    sendAction("继续探索");
  };
}

function disconnectCleanly() {
  // 发送 quit 后等服务端回复 quit_ok，然后关闭连接
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close(1000, "user quit");
  }
}

// ---- 存档面板 ----
function openSavePanel() {
  savePanelOverlay.classList.remove("hidden");
  safeSend(JSON.stringify({ type: "save_list" }));
}

function closeSavePanel() {
  savePanelOverlay.classList.add("hidden");
}

function renderSavePanel(saves: any[]) {
  if (!saves || saves.length === 0) {
    savePanelList.innerHTML = '<div class="save-empty">暂无存档</div>';
    return;
  }
  let html = "";
  for (const s of saves) {
    const isAuto = s.id === "slot_000";
    let timeStr = "未知时间";
    if (s.created_at) {
      try {
        const d = new Date(s.created_at);
        timeStr = d.toLocaleString("zh-CN", {
          year: "numeric", month: "2-digit", day: "2-digit",
          hour: "2-digit", minute: "2-digit"
        });
      } catch {}
    }
    const sceneName = s.scene_name || "未知场景";
    const hpStr = s.hp || "?";
    const sanStr = s.san || "?";
    const clueCount = s.clue_count ?? 0;
    const msgCount = s.message_count ?? 0;

    html += `<div class="save-slot-entry" data-slot="${s.id}">
      <div class="save-slot-info">
        <div class="save-slot-title">
          <span class="save-slot-name">${isAuto ? "💾 自动存档" : "📁 " + s.id}</span>
          <span class="save-slot-time">${timeStr}</span>
        </div>
        <div class="save-slot-meta">
          <span>${sceneName}</span>
          <span>HP ${hpStr}</span>
          <span>SAN ${sanStr}</span>
          <span>📜 ${clueCount}</span>
          <span>💬 ${msgCount}</span>
        </div>
      </div>
      <div class="save-slot-actions">
        <button class="save-action-load" data-slot="${s.id}">📂 加载</button>
        ${isAuto ? "" : `<button class="save-action-del" data-slot="${s.id}">🗑 删除</button>`}
      </div>
    </div>`;
  }
  savePanelList.innerHTML = html;

  // Bind load buttons
  savePanelList.querySelectorAll(".save-action-load").forEach(btn => {
    btn.addEventListener("click", () => {
      const slot = (btn as HTMLElement).getAttribute("data-slot") || "";
      closeSavePanel();
      addMsg("system", "正在读档…");
      safeSend(JSON.stringify({ type: "save_load", slot_id: slot }));
    });
  });

  // Bind delete buttons
  savePanelList.querySelectorAll(".save-action-del").forEach(btn => {
    btn.addEventListener("click", () => {
      const slot = (btn as HTMLElement).getAttribute("data-slot") || "";
      safeSend(JSON.stringify({ type: "save_delete", slot_id: slot }));
    });
  });
}

// ---- 按钮事件 ----
btnSend.onclick = () => {
  const text = userInput.value.trim();
  if (!text) return;
  sendAction(text);
  userInput.value = "";
};
userInput.onkeydown = (e) => {
  if (e.key === "Enter") {
    btnSend.click();
  }
};
btnSave.onclick = openSavePanel;
btnLoad.onclick = openSavePanel;
btnNew.onclick = () => location.reload();
btnPanel.onclick = () => {
  charPanel.classList.toggle("collapsed");
  if (!charPanel.classList.contains("collapsed")) loadState();
};
modalYes.onclick = () => sendSuggestReply(true);
modalNo.onclick = () => sendSuggestReply(false);
btnStart.onclick = startGame;
btnContinue.onclick = continueGame;
savePanelClose.onclick = closeSavePanel;
savePanelNew.onclick = () => {
  safeSend(JSON.stringify({ type: "save_create" }));
  addMsg("system", "正在保存…");
};
// Click outside panel to close
savePanelOverlay.onclick = (e) => {
  if (e.target === savePanelOverlay) closeSavePanel();
};

// ---- 启动 ----
// loadState 由 connect() 的 onopen 触发，无需在此调用
connect();
