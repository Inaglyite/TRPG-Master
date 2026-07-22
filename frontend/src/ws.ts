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

import { useAppStore, type ConnectionState } from "./state/app-store";
import {
  addMsg,
  attachTurnBranchAction,
  attachTurnRewriteAction,
  branchSourceTurnId,
  beginNarrativeReplacement,
  cancelNarrativeReplacement,
  completeNarrativeReplacement,
  removeTurnMessages,
  resetTurnActionButtons,
  renderTurnHistory,
  setDisplayTurnId,
  tagPendingPlayerMessage,
  whenNarrativePresented,
  whenNarrativeVisible,
  type TurnHistoryItem,
} from "./renderer";
import {
  onGmTurnStart,
  onStartTurnRejected,
  resetStartButton,
  getGameStarted,
  getGameStarting,
  onSaveList,
  onSaveAvailable,
  populateModuleList,
  populateCharacterList,
} from "./start";
import { onNarrativeChunk, onTension, onDice, onSummary } from "./renderer";
import { onNarrativeSegment, onNarrativeSegments } from "./renderer";
import {
  onSuggest,
  onDecision,
  onDecisionResolved,
  onDone,
  onTurnPhase,
  onConnectionLost,
  onConnectionRestored,
  type ActionChoice,
} from "./options";
import {
  finishQuickSave,
  closeSavePanel,
  showEnding,
  renderSavePanel,
  renderWorldPanel,
  updateCharPanel,
  updateCluePanel,
  showHandout,
  clearTransientHandouts,
} from "./panels";
import { applyTheme } from "./theme";
import { backendWebSocketUrl } from "./backend-url";
import {
  onModelSettings,
  onModelSettingsError,
  onTurnDiagnostics,
  onTurnPerformance,
} from "./settings";
import {
  onNotesWorldChanged,
  onPlayerNotes,
  onPlayerNotesConflict,
  onPlayerNotesError,
} from "./utility";
import { parseServerMessage } from "./protocol/server-message";

// ---- 后端地址 ----
const WS_BASE_URL = backendWebSocketUrl();
const WORLD_ID_KEY = "trpg-active-world-id";
const WORLD_MODULE_KEY = "trpg-active-world-module";
let preferredWorldId = localStorage.getItem(WORLD_ID_KEY) || "";
let preferredWorldModule = localStorage.getItem(WORLD_MODULE_KEY) || "";

function websocketUrl(): string {
  if (!preferredWorldId || !preferredWorldModule) return WS_BASE_URL;
  const params = new URLSearchParams({
    world_id: preferredWorldId,
    module: preferredWorldModule,
  });
  return `${WS_BASE_URL}?${params.toString()}`;
}

function rememberWorld(worldId: unknown, moduleName: unknown) {
  const id = String(worldId || "");
  const module = String(moduleName || "");
  if (!id || !module) return;
  preferredWorldId = id;
  preferredWorldModule = module;
  useAppStore.getState().setWorld(id, module);
  localStorage.setItem(WORLD_ID_KEY, id);
  localStorage.setItem(WORLD_MODULE_KEY, module);
}

// ---- WebSocket 实例 ----
let ws: WebSocket | null = null;

type HandoutMessage = Parameters<typeof showHandout>[0];

// 工具调用可能早于第一段玩家可见叙述。只缓存这一小段时间内的素材，
// 有了叙述上下文就立即展示，无需等到整轮 done。
let gmTurnActive = false;
let deferredHandouts: HandoutMessage[] = [];
let activeTurnId: string | null = null;
let activeBranchSourceTurnId: string | null = null;
let activeTurnKind: string | null = null;
let rewriteSourceTurnId: string | null = null;
let lastTurnSeq = 0;
let turnHasVisibleNarrative = false;
let narrativeVisibilityScheduled = false;
let pendingChoices: ActionChoice[] | undefined;
let interruptedTurn = false;
let interruptedTurnId: string | null = null;
let recoveryPending = false;
let recoveryPollTimer: number | null = null;
let outageActive = false;
let connectedOnce = false;
let manuallyClosing = false;
let reconnectAttempt = 0;
let reconnectTimer: number | null = null;
let noticeHideTimer: number | null = null;

const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000];

function setConn(connection: ConnectionState) {
  useAppStore.getState().setConnection(connection);
}

function showConnectionNotice(message: string, showRecovery = false) {
  if (noticeHideTimer !== null) {
    window.clearTimeout(noticeHideTimer);
    noticeHideTimer = null;
  }
  useAppStore.getState().setConnectionNotice(message, showRecovery);
}

function hideConnectionNotice(delay = 0) {
  if (noticeHideTimer !== null) window.clearTimeout(noticeHideTimer);
  noticeHideTimer = window.setTimeout(() => {
    useAppStore.getState().setConnectionNotice(null);
    noticeHideTimer = null;
  }, delay);
}

function clearRecoveryPoll() {
  if (recoveryPollTimer !== null) {
    window.clearTimeout(recoveryPollTimer);
    recoveryPollTimer = null;
  }
}

function requestTurnRecovery(delay = 0) {
  clearRecoveryPoll();
  recoveryPollTimer = window.setTimeout(() => {
    recoveryPollTimer = null;
    if (!ws || ws.readyState !== WebSocket.OPEN || !interruptedTurnId) return;
    ws.send(
      JSON.stringify({
        type: "turn_recovery_get",
        turn_id: interruptedTurnId,
      }),
    );
  }, delay);
}

function scheduleReconnect() {
  if (manuallyClosing || reconnectTimer !== null) return;
  const index = Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1);
  const delay = RECONNECT_DELAYS[index];
  reconnectAttempt += 1;
  const seconds = Math.max(1, Math.round(delay / 1000));
  const message = interruptedTurn
    ? `连接中断，本轮结果尚未确认。${seconds} 秒后重试连接……`
    : `连接已断开，${seconds} 秒后重试……`;
  showConnectionNotice(message);
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function acceptTurnEvent(data: any): boolean {
  if (data.type === "gm_turn_start") {
    activeTurnId = typeof data.turn_id === "string" ? data.turn_id : null;
    lastTurnSeq = Number.isFinite(data.seq) ? Number(data.seq) : 0;
    return true;
  }
  if (typeof data.turn_id !== "string") return true;
  if (data.turn_id !== activeTurnId) return false;
  const sequence = Number(data.seq);
  if (Number.isFinite(sequence)) {
    if (sequence <= lastTurnSeq) return false;
    lastTurnSeq = sequence;
  }
  return true;
}

function queueHandout(handout: HandoutMessage) {
  const data = handout as HandoutMessage & { asset_id?: string };
  const key = `${data.entity_type}:${data.entity_id}:${data.asset_id || data.file}`;
  const exists = deferredHandouts.some((item) => {
    const queued = item as HandoutMessage & { asset_id?: string };
    return (
      `${queued.entity_type}:${queued.entity_id}:${queued.asset_id || queued.file}` ===
      key
    );
  });
  if (!exists) deferredHandouts.push(handout);
}

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

function requestTurnRewrite(turnId: string) {
  safeSend(JSON.stringify({ type: "turn_rewrite", turn_id: turnId }));
}

function requestTurnBranch(turnId: string) {
  safeSend(JSON.stringify({ type: "turn_branch_create", turn_id: turnId }));
}

function attachTurnActions(history: TurnHistoryItem[]) {
  history.forEach((turn) => {
    const turnId = String(turn.turn_id || "");
    const sourceTurnId = branchSourceTurnId(turn);
    if (turnId && sourceTurnId) {
      attachTurnBranchAction(turnId, () => requestTurnBranch(sourceTurnId));
    }
  });
  const latestTurnId = String(history[history.length - 1]?.turn_id || "");
  if (latestTurnId) {
    attachTurnRewriteAction(latestTurnId, () =>
      requestTurnRewrite(latestTurnId),
    );
  }
}

function displayWorldHistory(rawHistory: unknown) {
  const history = Array.isArray(rawHistory)
    ? (rawHistory as TurnHistoryItem[])
    : [];
  const latest = renderTurnHistory(history);
  onDone(latest?.choices);
  attachTurnActions(history);
}

// ---- 连接 ----
export function connect() {
  if (
    ws &&
    (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)
  )
    return;
  setConn("connecting");
  const socket = new WebSocket(websocketUrl());
  ws = socket;
  socket.onopen = () => {
    if (ws !== socket) return;
    const wasOutage = outageActive;
    outageActive = false;
    reconnectAttempt = 0;
    setConn("connected");
    if (!connectedOnce) {
      connectedOnce = true;
      addMsg("system", "已连接到守秘人……");
    }
    socket.send(JSON.stringify({ type: "ping" }));
    // 排空连接前积压的消息
    while (sendQueue.length) socket.send(sendQueue.shift()!);
    // 连接建立后请求角色状态
    if (!interruptedTurn) socket.send(JSON.stringify({ type: "state" }));
    if (interruptedTurn) {
      onConnectionRestored(true);
      showConnectionNotice("连接已恢复，正在核对刚才回合的提交状态……");
      requestTurnRecovery();
    } else {
      onConnectionRestored(false);
      if (wasOutage) {
        showConnectionNotice("连接已恢复。");
        hideConnectionNotice(1600);
      } else {
        hideConnectionNotice();
      }
    }
  };
  socket.onmessage = handleMessage;
  socket.onclose = () => {
    if (ws !== socket) return;
    ws = null;
    const rewriteInterrupted = gmTurnActive && activeTurnKind === "rewrite";
    if (rewriteInterrupted && rewriteSourceTurnId) {
      cancelNarrativeReplacement(rewriteSourceTurnId);
    }
    interruptedTurn = interruptedTurn || (gmTurnActive && !rewriteInterrupted);
    if (gmTurnActive && activeTurnId && !rewriteInterrupted)
      interruptedTurnId = activeTurnId;
    if (!outageActive) {
      outageActive = true;
      if (interruptedTurn) clearTransientHandouts();
      onConnectionLost(interruptedTurn);
    }
    gmTurnActive = false;
    deferredHandouts = [];
    activeTurnId = null;
    activeTurnKind = null;
    rewriteSourceTurnId = null;
    lastTurnSeq = 0;
    turnHasVisibleNarrative = false;
    narrativeVisibilityScheduled = false;
    pendingChoices = undefined;
    setConn("disconnected");
    scheduleReconnect();
  };
  socket.onerror = () => setConn("disconnected");
}

// ---- 优雅断开 ----
export function disconnectCleanly() {
  manuallyClosing = true;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close(1000, "user quit");
  }
}

export function recoverLatestTurn() {
  if (!ws || ws.readyState !== WebSocket.OPEN || recoveryPending) return;
  recoveryPending = true;
  showConnectionNotice("正在恢复最近一个已完成回合……");
  ws.send(JSON.stringify({ type: "save_load", slot_id: "slot_000" }));
}

type PublicTurnRecord = {
  turn_id?: string;
  parent_turn_id?: string | null;
  status?: string;
  narrative?: string;
  narrative_segments?: import("./state/message-store").NarrativeSegment[];
  chat_events?: import("./state/message-store").ChatEvent[];
  choices?: ActionChoice[];
  events?: any[];
};

function replayRecoveredTurn(record: PublicTurnRecord) {
  const turnId = String(record.turn_id || interruptedTurnId || "");
  if (!turnId) return;
  clearRecoveryPoll();
  removeTurnMessages(turnId);
  clearTransientHandouts();
  setDisplayTurnId(turnId);
  gmTurnActive = true;
  turnHasVisibleNarrative = false;
  narrativeVisibilityScheduled = false;
  pendingChoices = Array.isArray(record.choices) ? record.choices : undefined;

  const events = Array.isArray(record.events) ? record.events : [];
  let replayedNarrative = false;
  for (const event of events) {
    switch (event?.type) {
      case "narrative_chunk":
        replayedNarrative =
          replayedNarrative || Boolean(String(event.text || "").trim());
        onNarrativeChunk(String(event.text || ""), event.npc_id);
        break;
      case "narrative_segment":
        onNarrativeSegment(event.segment?.speaker);
        break;
      case "tension":
        onTension(String(event.text || ""));
        break;
      case "dice_result":
        onDice(String(event.summary || ""), event.roll_data);
        break;
      case "glm_summary":
        onSummary(String(event.text || ""));
        break;
      case "handout":
        showHandout(event);
        break;
      case "error":
        addMsg("error", String(event.message || "本轮出现错误。"));
        break;
      case "choices":
        pendingChoices = Array.isArray(event.choices)
          ? event.choices
          : pendingChoices;
        break;
    }
  }
  if (!replayedNarrative && record.narrative) {
    onNarrativeChunk(record.narrative);
  }
  if (
    (Array.isArray(record.chat_events) && record.chat_events.length) ||
    (Array.isArray(record.narrative_segments) &&
      record.narrative_segments.length)
  ) {
    onNarrativeSegments(record.chat_events || record.narrative_segments || []);
  }
  onDone(pendingChoices);
  const branchSourceId = String(record.parent_turn_id || turnId);
  attachTurnBranchAction(turnId, () => requestTurnBranch(branchSourceId));
  attachTurnRewriteAction(turnId, () => requestTurnRewrite(turnId));
  gmTurnActive = false;
  setDisplayTurnId(null);
  activeTurnId = null;
  lastTurnSeq = 0;
  interruptedTurn = false;
  interruptedTurnId = null;
  recoveryPending = false;
  pendingChoices = undefined;
  hideConnectionNotice();
  addMsg("system", "已恢复刚才完整提交的回合。", true);
  safeSend(JSON.stringify({ type: "state" }));
}

function onTurnRecovery(data: any) {
  if (!interruptedTurn || !interruptedTurnId) return;
  const requested = data.requested as PublicTurnRecord | null;
  const active = data.active as PublicTurnRecord | null;
  const candidate =
    requested?.turn_id === interruptedTurnId
      ? requested
      : active?.turn_id === interruptedTurnId
        ? active
        : null;

  if (candidate?.status === "completed") {
    replayRecoveredTurn(candidate);
    return;
  }
  if (candidate?.status === "active") {
    showConnectionNotice("连接已恢复，守秘人仍在后台完成刚才的回合……");
    requestTurnRecovery(1500);
    return;
  }
  clearRecoveryPoll();
  showConnectionNotice(
    "刚才的回合没有完整提交，可以回到最近一个完整自动存档。",
    true,
  );
}

// ---- 消息分发 ----
function handleMessage(e: MessageEvent) {
  const parsed = parseServerMessage(e.data);
  if (!parsed) {
    addMsg("error", "守秘人发送了无法识别的协议消息，已安全忽略。", true);
    return;
  }
  const data: any = parsed;
  if (!acceptTurnEvent(data)) return;
  switch (data.type) {
    case "pong":
      break;
    case "gm_turn_start":
      gmTurnActive = true;
      activeTurnKind = String(data.turn_kind || "gameplay");
      rewriteSourceTurnId =
        activeTurnKind === "rewrite"
          ? String(data.source_turn_id || "") || null
          : null;
      setDisplayTurnId(activeTurnId);
      activeBranchSourceTurnId =
        String(data.parent_turn_id || activeTurnId || "") || null;
      if (activeTurnId && activeTurnKind !== "rewrite") {
        tagPendingPlayerMessage(activeTurnId);
      }
      deferredHandouts = [];
      turnHasVisibleNarrative = false;
      narrativeVisibilityScheduled = false;
      pendingChoices = undefined;
      if (rewriteSourceTurnId) beginNarrativeReplacement(rewriteSourceTurnId);
      onGmTurnStart();
      if (rewriteSourceTurnId) {
        onTurnPhase("守秘人正在重新组织叙述……");
      }
      break;
    case "turn_phase":
      onTurnPhase(String(data.label || "守秘人正在处理本轮行动……"));
      break;
    case "narrative_chunk":
      if (data.speaker) onNarrativeSegment(data.speaker);
      onNarrativeChunk(data.text, data.npc_id);
      if (!narrativeVisibilityScheduled && String(data.text || "").trim()) {
        narrativeVisibilityScheduled = true;
        whenNarrativeVisible(() => {
          turnHasVisibleNarrative = true;
          deferredHandouts.forEach((handout) => showHandout(handout));
          deferredHandouts = [];
        });
      }
      break;
    case "narrative_segment":
      onNarrativeSegment(data.segment?.speaker);
      break;
    case "narrative_segments":
      onNarrativeSegments(data.segments);
      break;
    case "chat_events":
      onNarrativeSegments(data.events);
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
    case "choices":
      pendingChoices = Array.isArray(data.choices) ? data.choices : undefined;
      break;
    case "done":
      const completedTurnId = activeTurnId;
      const completedBranchSourceTurnId = activeBranchSourceTurnId;
      onDone(pendingChoices);
      if (completedTurnId) {
        attachTurnBranchAction(completedTurnId, () =>
          requestTurnBranch(completedBranchSourceTurnId || completedTurnId),
        );
        attachTurnRewriteAction(completedTurnId, () =>
          requestTurnRewrite(completedTurnId),
        );
      }
      gmTurnActive = false;
      if (!narrativeVisibilityScheduled) {
        deferredHandouts.forEach((handout) => showHandout(handout));
        deferredHandouts = [];
      }
      activeTurnId = null;
      activeBranchSourceTurnId = null;
      activeTurnKind = null;
      rewriteSourceTurnId = null;
      setDisplayTurnId(null);
      lastTurnSeq = 0;
      turnHasVisibleNarrative = false;
      narrativeVisibilityScheduled = false;
      pendingChoices = undefined;
      if (recoveryPending || interruptedTurn) {
        recoveryPending = false;
        interruptedTurn = false;
        hideConnectionNotice();
      }
      safeSend(JSON.stringify({ type: "state" }));
      break;
    case "turn_rewritten": {
      const sourceTurnId = String(
        data.source_turn_id || rewriteSourceTurnId || "",
      );
      if (
        (Array.isArray(data.chat_events) && data.chat_events.length) ||
        (Array.isArray(data.narrative_segments) &&
          data.narrative_segments.length)
      ) {
        onNarrativeSegments(data.chat_events || data.narrative_segments);
      }
      pendingChoices = Array.isArray(data.choices)
        ? data.choices
        : pendingChoices;
      onDone(pendingChoices);
      if (sourceTurnId) {
        whenNarrativePresented(() =>
          completeNarrativeReplacement(sourceTurnId),
        );
      }
      if (sourceTurnId) {
        const branchSourceId = String(
          data.branch_source_turn_id || sourceTurnId,
        );
        attachTurnBranchAction(sourceTurnId, () =>
          requestTurnBranch(branchSourceId),
        );
        attachTurnRewriteAction(sourceTurnId, () =>
          requestTurnRewrite(sourceTurnId),
        );
      }
      gmTurnActive = false;
      deferredHandouts = [];
      activeTurnId = null;
      activeTurnKind = null;
      rewriteSourceTurnId = null;
      setDisplayTurnId(null);
      lastTurnSeq = 0;
      turnHasVisibleNarrative = false;
      narrativeVisibilityScheduled = false;
      pendingChoices = undefined;
      addMsg("system", "叙述已更新；判定、线索与世界状态均未改变。", true);
      safeSend(JSON.stringify({ type: "state" }));
      break;
    }
    case "turn_rewrite_failed": {
      const sourceTurnId = String(
        data.source_turn_id || rewriteSourceTurnId || "",
      );
      if (sourceTurnId) cancelNarrativeReplacement(sourceTurnId);
      onDone();
      if (sourceTurnId) {
        const branchSourceId = String(
          data.branch_source_turn_id || sourceTurnId,
        );
        attachTurnBranchAction(sourceTurnId, () =>
          requestTurnBranch(branchSourceId),
        );
        attachTurnRewriteAction(sourceTurnId, () =>
          requestTurnRewrite(sourceTurnId),
        );
      }
      gmTurnActive = false;
      deferredHandouts = [];
      activeTurnId = null;
      activeTurnKind = null;
      rewriteSourceTurnId = null;
      setDisplayTurnId(null);
      lastTurnSeq = 0;
      turnHasVisibleNarrative = false;
      pendingChoices = undefined;
      addMsg("error", data.message || "重新叙述失败，原叙述已保留。", true);
      break;
    }
    case "turn_recovery":
      onTurnRecovery(data);
      break;
    case "world_context":
      rememberWorld(data.world_id, data.module_name);
      onNotesWorldChanged();
      break;
    case "world_list":
      renderWorldPanel(data.worlds || [], String(data.active_world_id || ""));
      break;
    case "turn_branched":
      rememberWorld(data.world_id, data.module_name);
      if (getGameStarted()) displayWorldHistory(data.history);
      closeSavePanel();
      addMsg(
        "system",
        `已进入「${data.label || "新的时间线"}」，原时间线保持不变。`,
        true,
      );
      safeSend(JSON.stringify({ type: "state" }));
      break;
    case "turn_branch_failed":
      resetTurnActionButtons();
      addMsg("error", data.message || "创建时间线分支失败。", true);
      break;
    case "world_switched":
      rememberWorld(data.world_id, data.module_name);
      if (getGameStarted()) {
        displayWorldHistory(data.history);
        addMsg("system", "已切换时间线。", true);
      }
      closeSavePanel();
      safeSend(JSON.stringify({ type: "state" }));
      break;
    case "world_switch_failed":
      addMsg("error", data.message || "切换时间线失败。", true);
      safeSend(JSON.stringify({ type: "world_list" }));
      break;
    case "player_notes":
      onPlayerNotes(data);
      break;
    case "player_notes_conflict":
      onPlayerNotesConflict(data);
      break;
    case "player_notes_error":
      onPlayerNotesError(data.message);
      break;
    case "turn_rejected":
      resetTurnActionButtons();
      if (
        onStartTurnRejected(
          String(data.message || "上一局仍在收尾，请稍候。"),
          data.reason === "world_turn_in_progress" ||
            data.reason === "turn_finalizing",
        )
      ) {
        break;
      }
      addMsg("error", data.message || "上一回合尚未结束，请稍候。", true);
      if (recoveryPending) {
        recoveryPending = false;
        showConnectionNotice(
          "上一回合仍在后台收尾。稍候片刻，再恢复最近自动存档。",
          true,
        );
      } else if (data.reason === "turn_finalizing") {
        onDone();
      }
      break;
    case "error":
      addMsg("error", data.message);
      const startErrorHandled = onStartTurnRejected(
        String(data.message || "新游戏启动失败，请重试。"),
        false,
      );
      if (recoveryPending) {
        recoveryPending = false;
        showConnectionNotice(
          "最近自动存档无法恢复。你可以重试，或从顶部的新游戏按钮返回主菜单。",
          true,
        );
      }
      if (!startErrorHandled && (!getGameStarted() || getGameStarting())) {
        resetStartButton();
      }
      break;
    case "saved":
      if (data.slot_id === "slot_000") finishQuickSave(Boolean(data.ok));
      addMsg(
        data.ok ? "system" : "error",
        data.ok
          ? data.slot_id === "slot_000"
            ? "进度已快速保存。"
            : "新存档已创建。"
          : "存档失败，请稍后再试。",
      );
      if (useAppStore.getState().savePanelOpen) {
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
      rememberWorld(data.world_id, data.module_name || data.active);
      populateModuleList(data.modules, data.active);
      break;
    case "character_list":
      populateCharacterList(data.groups || []);
      break;
    case "theme":
      applyTheme(data.theme);
      break;
    case "model_settings":
      onModelSettings(data);
      break;
    case "model_settings_error":
      onModelSettingsError(data.message);
      break;
    case "turn_diagnostics":
      onTurnDiagnostics(data.diagnostics);
      break;
    case "turn_performance":
      onTurnPerformance(data.metrics);
      break;
    case "save_list":
      onSaveList(data);
      renderSavePanel(data.saves || []);
      break;
    case "save_available":
      onSaveAvailable(data);
      break;
    case "loaded":
      addMsg(
        "system",
        data.ok ? `读档成功，恢复了 ${data.count} 条消息。` : "未找到存档。",
      );
      if (recoveryPending && data.ok) {
        showConnectionNotice("进度已恢复，守秘人正在重建当前场景……");
      }
      break;
    case "case_settled":
      addMsg(
        "system",
        data.ok
          ? "案件经历已写入调查员长期履历。"
          : `履历写入失败：${data.error || "未知错误"}`,
      );
      break;
    case "character_state":
      // 新游戏/读档确认后立即采用服务端的权威角色，避免显示静态占位角色。
      updateCharPanel(data.data);
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
      if (gmTurnActive && !turnHasVisibleNarrative) {
        queueHandout(data);
      } else {
        showHandout(data);
        if (!gmTurnActive) safeSend(JSON.stringify({ type: "state" }));
      }
      break;
  }
}
