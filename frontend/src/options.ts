/**
 * options.ts — 选项与用户输入
 *
 * 负责：onDone() 解析选项、renderOptions() 渲染按钮、enableInput() 切换输入框、
 *       sendAction() / sendSuggestReply() 发送回复、onSuggest() 显示检定弹窗。
 * 同时绑定发送框和弹窗按钮的事件。
 */

import {
  messagesEl,
  optionsBar,
  userInput,
  btnSend,
  modalOverlay,
  modalText,
  modalActions,
  modalYes,
  modalNo,
} from "./dom";
import {
  addMsg,
  removeLoading,
  removeRollPending,
  showGmThinking,
  showRollPending,
  getStreamTarget,
  setStreamTarget,
} from "./renderer";
import { safeSend } from "./ws";
import { escapeHtml } from "./text";

let activeDecisionId: string | null = null;

// ---- 建议检定弹窗 ----
export function onSuggest(data: any) {
  removeLoading();
  modalActions.replaceChildren(modalYes, modalNo);
  modalText.innerHTML = `
    <div class="suggest-desc">${escapeHtml(data.description)}</div>
    <div class="suggest-roll">
      <b>${escapeHtml(data.skill)}</b>（${escapeHtml(data.attribute)}）
      — 难度：${escapeHtml(data.dc_label)}（DC ${escapeHtml(data.dc)}）
    </div>
  `;
  modalOverlay.classList.remove("hidden");
  modalYes.focus();
}

// ---- 通用多选决定（战斗防御等） ----
export function onDecision(data: any) {
  removeLoading();
  activeDecisionId = data.id || null;
  const options = Array.isArray(data.options) ? data.options : [];
  modalText.innerHTML = `
    <div class="decision-title">${escapeHtml(data.title || "需要你做出决定")}</div>
    <div class="suggest-desc">${escapeHtml(data.description || "")}</div>
  `;
  modalActions.replaceChildren();

  options.forEach((option: any) => {
    const button = document.createElement("button");
    const isDangerous = ["confirm_violence", "confirm_threat", "fight_back", "no_defense"].includes(option.id);
    button.className = `${isDangerous ? "btn-danger" : "btn-safe"} decision-option`;
    const label = document.createElement("strong");
    label.textContent = option.label || option.id;
    button.appendChild(label);
    if (option.description) {
      const description = document.createElement("span");
      description.textContent = option.description;
      button.appendChild(description);
    }
    button.onclick = () => sendDecisionReply(data.id, option.id, option.label || option.id);
    modalActions.appendChild(button);
  });

  modalOverlay.classList.remove("hidden");
  (modalActions.querySelector("button") as HTMLButtonElement | null)?.focus();
}

// ---- GM 叙述结束，解析选项 ----
export function onDone() {
  removeLoading();
  removeRollPending();
  // 结束流式光标
  if (getStreamTarget()) {
    getStreamTarget()!.classList.remove("streaming-cursor");
    setStreamTarget(null);
  }
  // 解析选项：从最近的 GM 消息中提取（跳过骰子、摘要等非叙事消息）
  const opts: { label: string; isFree: boolean }[] = [];
  const children = messagesEl.children;
  for (let i = children.length - 1; i >= 0; i--) {
    const el = children[i];
    if (!el.classList.contains("gm")) continue;

    // 优先从最后一个 <ol> 提取（marked 渲染的编号列表），
    // 且该 <ol> 必须在"你可以"之后出现，避免把叙事中的编号列表误判为选项
    const olElements = el.querySelectorAll("ol");
    if (olElements.length > 0) {
      const html = el.innerHTML;
      // 找到"你可以"在 HTML 中的位置
      const markerMatch = html.match(/你可以|您可以/);
      const markerIdx = markerMatch && markerMatch.index != null ? markerMatch.index : -1;

      // 取最后一个 <ol>，但如果它与"你可以"的距离合理（在其之后），就用它
      let targetOl: Element | null = null;
      for (let i = olElements.length - 1; i >= 0; i--) {
        const olHtml = olElements[i].outerHTML;
        const olIdx = html.indexOf(olHtml);
        if (markerIdx < 0 || olIdx > markerIdx) {
          targetOl = olElements[i];
          break;
        }
      }
      // 如果没找到"你可以"之后的 <ol>，就用最后一个
      if (!targetOl) targetOl = olElements[olElements.length - 1];

      const liElements = targetOl.querySelectorAll("li");
      liElements.forEach((li) => {
        const label = (li.textContent || "").trim();
        if (label) {
          const isFree = label.includes("自由行动") || label.includes("你决定做什么");
          opts.push({ label, isFree });
        }
      });
    }

    // 降级：<br> 分隔的纯文本格式
    if (opts.length === 0) {
      const text = el.textContent || "";
      const lines = text.split("\n");
      let startIdx = 0;
      for (let j = lines.length - 1; j >= 0; j--) {
        if (/你可以|您可以/.test(lines[j])) { startIdx = j; break; }
      }
      for (let j = startIdx; j < lines.length; j++) {
        const m = lines[j].trim().match(/^\d+\.\s*(.+)/);
        if (m) {
          const label = m[1].trim();
          const isFree = label.includes("自由行动") || label.includes("你决定做什么");
          opts.push({ label, isFree });
        }
      }
    }
    break; // 只查最近一条 GM 消息
  }

  if (opts.length > 0) {
    renderOptions(opts);
  } else {
    // 没解析出选项，显示输入框
    optionsBar.innerHTML = "";
    enableInput(true);
  }
}

// ---- 渲染选项按钮 ----
export function renderOptions(opts: { label: string; isFree: boolean }[]) {
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
  const hasFree = opts.some((o) => o.isFree);
  enableInput(hasFree);
}

// ---- 启用 / 禁用输入 ----
export function enableInput(on: boolean) {
  userInput.disabled = !on;
  btnSend.disabled = !on;
  userInput.placeholder = on ? "你决定做什么？" : "守秘人正在叙述……";
  if (on) userInput.focus();
}

// ---- 发送行动 ----
export function sendAction(text: string) {
  addMsg("player", text, true);
  optionsBar.innerHTML = "";
  enableInput(false);
  showGmThinking();
  safeSend(JSON.stringify({ type: "action", content: text }));
}

// ---- 发送建议检定回复 ----
export function sendSuggestReply(confirmed: boolean) {
  modalOverlay.classList.add("hidden");
  if (confirmed) {
    addMsg("player", "🎲 确定尝试！", true);
    showRollPending();
  } else {
    addMsg("player", "↩ 放弃行动。", true);
  }
  safeSend(JSON.stringify({ type: "suggest_reply", confirmed }));
}

export function sendDecisionReply(decisionId: string, optionId: string, label: string) {
  modalOverlay.classList.add("hidden");
  activeDecisionId = null;
  addMsg("player", label, true);
  if (optionId === "confirm_threat") {
    showGmThinking();
  } else if (!["cancel_violence", "cancel_threat"].includes(optionId)) {
    showRollPending();
  }
  safeSend(JSON.stringify({
    type: "decision_reply",
    decision_id: decisionId,
    option_id: optionId,
  }));
}

export function onDecisionResolved(data: any) {
  if (!data.automatic || data.decision_id !== activeDecisionId) return;
  modalOverlay.classList.add("hidden");
  activeDecisionId = null;
  if (["cancel_violence", "cancel_threat"].includes(data.option_id)) {
    const action = data.option_id === "cancel_threat" ? "威胁" : "攻击";
    addMsg("system", `等待确认超时，已取消这次${action}。`, true);
  } else {
    addMsg("system", "等待选择超时，已采用默认防御。", true);
    showRollPending();
  }
}

// ==================== 按钮事件绑定 ====================

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

modalYes.onclick = () => sendSuggestReply(true);
modalNo.onclick = () => sendSuggestReply(false);
