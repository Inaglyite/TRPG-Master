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
  getGameStarted,
  getGameStarting,
  onSaveList,
  onSaveAvailable,
  populateModuleList,
  populateCharacterList,
} from "./start";
import { onNarrativeChunk, onTension, onDice, onSummary } from "./renderer";
import { onSuggest, onDecision, onDecisionResolved, onDone } from "./options";
import {
  finishQuickSave,
  showEnding,
  renderSavePanel,
  updateCharPanel,
  updateCluePanel,
  showHandout,
} from "./panels";
import { applyTheme } from "./main";

// ---- 后端地址 ----
const WS_HOST = location.hostname || "localhost";
const WS_URL = `ws://${WS_HOST}:8765/ws`;

// ---- WebSocket 实例 ----
let ws: WebSocket | null = null;

type HandoutMessage = Parameters<typeof showHandout>[0];

// 工具调用可能早于本轮最终叙述完成。回合进行中先缓存会剧透的 UI 更新，
// 等 done 到达后再展示，避免图片和线索抢在守秘人叙述前出现。
let gmTurnActive = false;
let deferredHandouts: HandoutMessage[] = [];

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
    gmTurnActive = false;
    deferredHandouts = [];
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
      gmTurnActive = true;
      deferredHandouts = [];
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
    case "decision_request":
      onDecision(data);
      break;
    case "decision_resolved":
      onDecisionResolved(data);
      break;
    case "done":
      onDone();
      gmTurnActive = false;
      deferredHandouts.forEach((handout) => showHandout(handout));
      deferredHandouts = [];
      safeSend(JSON.stringify({ type: "state" }));
      break;
    case "error":
      addMsg("error", data.message);
      if (getGameStarting()) resetStartButton();
      break;
    case "saved":
      if (data.slot_id === "slot_000") finishQuickSave(Boolean(data.ok));
      addMsg(
        data.ok ? "system" : "error",
        data.ok
          ? (data.slot_id === "slot_000" ? "进度已快速保存。" : "新存档已创建。")
          : "存档失败，请稍后再试。",
      );
      if (!savePanelOverlay.classList.contains("hidden")) {
        safeSend(JSON.stringify({ type: "save_list" }));
      }
      break;
    case "save_deleted":
      addMsg("system", "存档已删除。");
      safeSend(JSON.stringify({ type: "save_list" }));
      break;
    case "save_renamed":
      addMsg(
        data.ok ? "system" : "error",
        data.ok
          ? `存档已重命名为「${data.label || "场景默认名称"}」。`
          : "存档重命名失败，请稍后再试。",
      );
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
      // 开场前的预取状态可能属于旧存档；回合中的状态又可能早于叙述。
      // done 后会重新请求最终状态，因此这两种响应都不应提前渲染。
      if (getGameStarted() && !gmTurnActive) {
        updateCharPanel(data.data);
        updateCluePanel(data.clues);
      }
      break;
    case "handout":
      if (gmTurnActive) {
        deferredHandouts.push(data);
      } else {
        showHandout(data);
        safeSend(JSON.stringify({ type: "state" }));
      }
      break;
  }
}
