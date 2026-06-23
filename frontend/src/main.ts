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
    case "saved": addMsg("system", data.ok ? "存档成功。" : "存档失败。"); break;
    case "save_available": onSaveAvailable(data); break;
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
  if (gameStarting) return;
  gameStarting = true;
  btnStart.disabled = true;
  btnContinue.disabled = true;
  btnContinue.textContent = "正在读档……";
  safeSend(JSON.stringify({ type: "continue" }));
}

function onSaveAvailable(data: any) {
  if (data.has_save) {
    btnContinue.classList.remove("hidden");
    btnStart.textContent = "🕯 开始新游戏";
  }
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
  // 解析选项
  const lastMsg = messagesEl.lastElementChild;
  if (!lastMsg) return;

  const text = lastMsg.innerHTML;
  const lines = text.split("<br>");
  const opts: { label: string; isFree: boolean }[] = [];

  for (const line of lines) {
    // 匹配 "1. xxx" 或 "1. **xxx**" 格式
    const m = line.match(/^\d+\.\s*(.+)/);
    if (m) {
      const label = m[1].replace(/<[^>]+>/g, "");
      const isFree = label.includes("自由行动") || label.includes("你决定做什么");
      opts.push({ label, isFree });
    }
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
btnSave.onclick = () => safeSend(JSON.stringify({ type: "save" }));
btnLoad.onclick = () => safeSend(JSON.stringify({ type: "load" }));
btnNew.onclick = () => location.reload();
btnPanel.onclick = () => {
  charPanel.classList.toggle("collapsed");
  if (!charPanel.classList.contains("collapsed")) loadState();
};
modalYes.onclick = () => sendSuggestReply(true);
modalNo.onclick = () => sendSuggestReply(false);
btnStart.onclick = startGame;
btnContinue.onclick = continueGame;

// ---- 启动 ----
// loadState 由 connect() 的 onopen 触发，无需在此调用
connect();
