/**
 * ws.ts — WebSocket 通信
 *
 * 管理连接生命周期、消息收发、重连逻辑。
 * handleMessage() 收到服务端消息后分发到各模块的处理函数。
 *
 * 注意：此模块与 options / panels / start 存在循环引用，
 *      但所有交叉引用值均仅在 handleMessage（异步回调）中使用，
 *      ES 模块的 live binding 机制保证了模块初始化完成后值立即可用。
 */

import { setConn, savePanelOverlay } from "./dom";
import { addMsg } from "./renderer";
import {
  onGmTurnStart,
  resetStartButton,
  getGameStarting,
  onSaveList,
  onSaveAvailable,
  populateModuleList,
  populateCharacterList,
} from "./start";
import { onNarrativeChunk, onTension, onDice, onSummary } from "./renderer";
import { onSuggest, onDone } from "./options";
import { showEnding, renderSavePanel, updateCharPanel, updateCluePanel, showHandout } from "./panels";
import { applyTheme } from "./main";

// ---- 后端地址 ----
const WS_HOST = location.hostname || "localhost";
const WS_URL = `ws://${WS_HOST}:8765/ws`;

// ---- WebSocket 实例 ----
let ws: WebSocket | null = null;

export function getWs(): WebSocket | null {
  return ws;
}

export function setWs(w: WebSocket) {
  ws = w;
}

// ---- 待发送队列（连接未就绪时缓存） ----
const sendQueue: string[] = [];

export function safeSend(payload: string) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(payload);
  } else {
    sendQueue.push(payload);
  }
}

// ---- 连接 ----
export function connect() {
  setConn("connecting");
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    setConn("connected");
    addMsg("system", "已连接到守秘人……");
    ws!.send(JSON.stringify({ type: "ping" }));
    // 排空连接前积压的消息
    while (sendQueue.length) ws!.send(sendQueue.shift()!);
    // 连接建立后请求角色状态
    ws!.send(JSON.stringify({ type: "state" }));
  };
  ws.onmessage = handleMessage;
  ws.onclose = () => {
    setConn("disconnected");
    addMsg("error", "连接断开。5秒后重试……");
    setTimeout(connect, 5000);
  };
  ws.onerror = () => setConn("disconnected");
}

// ---- 优雅断开 ----
export function disconnectCleanly() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close(1000, "user quit");
  }
}

// ---- 消息分发 ----
function handleMessage(e: MessageEvent) {
  const data = JSON.parse(e.data);
  switch (data.type) {
    case "pong":
      break;
    case "gm_turn_start":
      onGmTurnStart();
      break;
    case "narrative_chunk":
      onNarrativeChunk(data.text);
      break;
    case "tension":
      onTension(data.text);
      break;
    case "dice_result":
      onDice(data.summary, data.roll_data);
      break;
    case "glm_summary":
      onSummary(data.text);
      break;
    case "suggest_check":
      onSuggest(data);
      break;
    case "done":
      onDone();
      safeSend(JSON.stringify({ type: "state" }));
      break;
    case "error":
      addMsg("error", data.message);
      if (getGameStarting()) resetStartButton();
      break;
    case "saved":
      addMsg("system", data.ok ? `存档成功 (${data.slot_id})。` : "存档失败。");
      if (!savePanelOverlay.classList.contains("hidden")) {
        safeSend(JSON.stringify({ type: "save_list" }));
      }
      break;
    case "save_deleted":
      addMsg("system", "存档已删除。");
      safeSend(JSON.stringify({ type: "save_list" }));
      break;
    case "save_renamed":
      addMsg("system", `存档已重命名为「${data.label || "(默认)"}」。`);
      safeSend(JSON.stringify({ type: "save_list" }));
      break;
    case "quit_ok":
      addMsg("system", "进度已保存。");
      disconnectCleanly();
      break;
    case "game_over":
      showEnding(data);
      break;
    case "module_list":
      populateModuleList(data.modules, data.active);
      break;
    case "character_list":
      populateCharacterList(data.groups || []);
      break;
    case "theme":
      applyTheme(data.theme);
      break;
    case "save_list":
      onSaveList(data);
      if (!savePanelOverlay.classList.contains("hidden")) {
        renderSavePanel(data.saves || []);
      }
      break;
    case "save_available":
      onSaveAvailable(data);
      break;
    case "loaded":
      addMsg("system", data.ok ? `读档成功，恢复了 ${data.count} 条消息。` : "未找到存档。");
      break;
    case "case_settled":
      addMsg("system", data.ok ? "案件经历已写入调查员长期履历。" : `履历写入失败：${data.error || "未知错误"}`);
      break;
    case "state_data":
      updateCharPanel(data.data);
      updateCluePanel(data.clues);
      break;
    case "handout":
      showHandout(data);
      safeSend(JSON.stringify({ type: "state" }));
      break;
  }
}
