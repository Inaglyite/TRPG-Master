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
  modalYes,
  modalNo,
} from "./dom";
import { addMsg, removeLoading, showGmThinking, getStreamTarget, setStreamTarget } from "./renderer";
import { safeSend } from "./ws";

// ---- 建议检定弹窗 ----
export function onSuggest(data: any) {
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

// ---- GM 叙述结束，解析选项 ----
export function onDone() {
  removeLoading();
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
  addMsg("player", text);
  optionsBar.innerHTML = "";
  enableInput(false);
  showGmThinking();
  safeSend(JSON.stringify({ type: "action", content: text }));
}

// ---- 发送建议检定回复 ----
export function sendSuggestReply(confirmed: boolean) {
  modalOverlay.classList.add("hidden");
  if (confirmed) {
    addMsg("player", "🎲 确定尝试！");
  } else {
    addMsg("player", "↩ 放弃行动。");
  }
  safeSend(JSON.stringify({ type: "suggest_reply", confirmed }));
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
