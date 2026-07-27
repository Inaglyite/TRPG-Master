/**
 * options.ts — 选项与用户输入
 *
 * 负责：onDone() 解析选项、renderOptions() 渲染按钮、enableInput() 切换输入框、
 *       sendAction() / sendSuggestReply() 发送回复、onSuggest() 显示检定弹窗。
 * 同时绑定发送框和弹窗按钮的事件。
 */

import {
  addMsg,
  removeLoading,
  removeRollPending,
  showGmThinking,
  showRollPending,
  finishNarrativeStream,
  flushNarrativeStream,
  whenNarrativePresented,
} from "./renderer";
import { safeSend } from "./ws";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { canCurrentUserAct, useOnlineStore } from "./state/online-store";

let activeDecisionId: string | null = null;
let inputEnabledBeforeDisconnect = false;
let pendingOptimisticAction: {
  messageId: string;
  choices: ReturnType<typeof useAppStore.getState>["choices"];
  dialog: ReturnType<typeof useAppStore.getState>["dialog"];
  ending: ReturnType<typeof useAppStore.getState>["ending"];
  inputEnabled: boolean;
  inputPlaceholder: string;
} | null = null;

export type ActionChoice = {
  label: string;
  isFree: boolean;
};

// ---- 建议检定弹窗 ----
export function onSuggest(data: any) {
  flushNarrativeStream();
  removeLoading();
  useAppStore.getState().setDialog({ kind: "suggest", ...data });
}

// ---- 通用多选决定（战斗防御等） ----
export function onDecision(data: any) {
  flushNarrativeStream();
  removeLoading();
  activeDecisionId = data.id || null;
  const options = Array.isArray(data.options) ? data.options : [];
  useAppStore.getState().setDialog({
    kind: "decision",
    id: String(data.id || ""),
    title: data.title,
    description: data.description,
    options,
  });
}

// ---- GM 叙述结束，解析选项 ----
export function onDone(structuredChoices?: ActionChoice[]) {
  removeLoading();
  removeRollPending();
  finishNarrativeStream();
  whenNarrativePresented(() => completePresentedTurn(structuredChoices));
}

function completePresentedTurn(structuredChoices?: ActionChoice[]) {
  // 解析选项：从最近的 GM 消息中提取（跳过骰子、摘要等非叙事消息）
  const opts: ActionChoice[] = Array.isArray(structuredChoices)
    ? structuredChoices.filter(
        (choice) =>
          choice && typeof choice.label === "string" && choice.label.trim(),
      )
    : [];
  if (opts.length === 0) {
    const messages = useMessageStore.getState().messages;
    let narrative = "";
    for (let index = messages.length - 1; index >= 0; index--) {
      if (messages[index].kind === "gm") {
        narrative = messages[index].text;
        break;
      }
    }
    const marker = Math.max(
      narrative.lastIndexOf("你可以"),
      narrative.lastIndexOf("您可以"),
    );
    if (marker >= 0) {
      narrative
        .slice(marker)
        .split("\n")
        .forEach((line) => {
          const match = line.trim().match(/^\d+[.、]\s*(.+)/);
          if (!match) return;
          const label = match[1].trim();
          opts.push({
            label,
            isFree:
              label.includes("自由行动") || label.includes("你决定做什么"),
          });
        });
    }
  }

  if (opts.length > 0) {
    renderOptions(opts);
  } else {
    // 没解析出选项，显示输入框
    useAppStore.getState().setChoices([]);
    enableInput(true);
  }
}

// ---- 渲染选项按钮 ----
export function renderOptions(opts: { label: string; isFree: boolean }[]) {
  useAppStore.getState().setChoices(opts);
  useAppStore.getState().setEnding(null);
  // 自由行动时同时启用输入框
  const hasFree = opts.some((o) => o.isFree);
  enableInput(hasFree);
}

// ---- 启用 / 禁用输入 ----
export function enableInput(on: boolean) {
  useAppStore
    .getState()
    .setInput(on, on ? "你决定做什么？" : "守秘人正在叙述……");
}

export function onTurnPhase(label: string) {
  if (useAppStore.getState().inputEnabled) return;
  useAppStore.getState().setInput(false, label || "守秘人正在处理本轮行动……");
  showGmThinking(label || "守秘人正在处理本轮行动……");
}

export function onConnectionLost(turnInterrupted: boolean) {
  inputEnabledBeforeDisconnect = useAppStore.getState().inputEnabled;
  flushNarrativeStream();
  removeLoading();
  removeRollPending();
  useAppStore.getState().setDialog(null);
  activeDecisionId = null;
  finishNarrativeStream();
  if (turnInterrupted) useAppStore.getState().setChoices([]);
  enableInput(false);
  useAppStore
    .getState()
    .setInput(
      false,
      turnInterrupted
        ? "本轮连接中断，重连后可恢复进度"
        : "连接已断开，正在重试……",
    );
}

export function onConnectionRestored(recoveryRequired: boolean) {
  if (recoveryRequired) {
    enableInput(false);
    useAppStore.getState().setInput(false, "请选择恢复最近自动存档");
    return;
  }
  enableInput(inputEnabledBeforeDisconnect);
}

function onlineActionAllowed(): boolean {
  if (useAppStore.getState().mode !== "online") return true;
  if (canCurrentUserAct()) return true;
  useOnlineStore.setState({ roomError: "还没有轮到你行动" });
  return false;
}

function rememberOptimisticAction(messageId: string): void {
  const state = useAppStore.getState();
  pendingOptimisticAction = {
    messageId,
    choices: [...state.choices],
    dialog: state.dialog,
    ending: state.ending,
    inputEnabled: state.inputEnabled,
    inputPlaceholder: state.inputPlaceholder,
  };
}

/** 服务端已开始处理该操作，后续不能再被无关拒绝事件回滚。 */
export function acknowledgePendingAction(): void {
  pendingOptimisticAction = null;
}

/** 回滚服务端拒绝前的乐观气泡、等待动画和输入状态。 */
export function rollbackPendingAction(): boolean {
  const pending = pendingOptimisticAction;
  if (!pending) return false;
  pendingOptimisticAction = null;
  removeLoading();
  removeRollPending();
  useMessageStore
    .getState()
    .updateMessages((messages) =>
      messages.filter((message) => message.id !== pending.messageId),
    );
  useAppStore.setState({
    choices: pending.choices,
    dialog: pending.dialog,
    ending: pending.ending,
    inputEnabled: pending.inputEnabled,
    inputPlaceholder: pending.inputPlaceholder,
  });
  return true;
}

// ---- 发送行动 ----
export function sendAction(text: string) {
  if (!onlineActionAllowed() || pendingOptimisticAction) return;
  const messageId = addMsg("player", text, true);
  rememberOptimisticAction(messageId);
  useAppStore.getState().setChoices([]);
  useAppStore.getState().setEnding(null);
  enableInput(false);
  showGmThinking();
  safeSend(JSON.stringify({ type: "action", content: text }));
}

// ---- 发送建议检定回复 ----
export function sendSuggestReply(confirmed: boolean) {
  if (!onlineActionAllowed() || pendingOptimisticAction) return;
  const previous = useAppStore.getState();
  useAppStore.getState().setDialog(null);
  const messageId = confirmed
    ? addMsg("player", "🎲 确定尝试！", true)
    : addMsg("player", "↩ 放弃行动。", true);
  pendingOptimisticAction = {
    messageId,
    choices: [...previous.choices],
    dialog: previous.dialog,
    ending: previous.ending,
    inputEnabled: previous.inputEnabled,
    inputPlaceholder: previous.inputPlaceholder,
  };
  if (confirmed) {
    showRollPending();
  }
  safeSend(JSON.stringify({ type: "suggest_reply", confirmed }));
}

export function sendDecisionReply(
  decisionId: string,
  optionId: string,
  label: string,
) {
  if (!onlineActionAllowed() || pendingOptimisticAction) return;
  const previous = useAppStore.getState();
  useAppStore.getState().setDialog(null);
  activeDecisionId = null;
  const messageId = addMsg("player", label, true);
  pendingOptimisticAction = {
    messageId,
    choices: [...previous.choices],
    dialog: previous.dialog,
    ending: previous.ending,
    inputEnabled: previous.inputEnabled,
    inputPlaceholder: previous.inputPlaceholder,
  };
  if (optionId === "confirm_threat") {
    showGmThinking();
  } else if (!["cancel_violence", "cancel_threat"].includes(optionId)) {
    showRollPending();
  }
  safeSend(
    JSON.stringify({
      type: "decision_reply",
      decision_id: decisionId,
      option_id: optionId,
    }),
  );
}

export function onDecisionResolved(data: any) {
  if (!data.automatic || data.decision_id !== activeDecisionId) return;
  useAppStore.getState().setDialog(null);
  activeDecisionId = null;
  if (["cancel_violence", "cancel_threat"].includes(data.option_id)) {
    const action = data.option_id === "cancel_threat" ? "威胁" : "攻击";
    addMsg("system", `等待确认超时，已取消这次${action}。`, true);
  } else {
    addMsg("system", "等待选择超时，已采用默认防御。", true);
    showRollPending();
  }
}
