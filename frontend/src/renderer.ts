/**
 * renderer.ts — 消息渲染
 *
 * 负责：添加消息气泡、流式文本追加、滚动、加载指示器（守秘人思考中）。
 * 流式输出目标 streamTarget 通过 getter/setter 供 options.ts 访问。
 */

import { marked } from "marked";
import { messagesEl } from "./dom";

// ---- Markdown 解析器（一次性配置） ----
// 禁用图片渲染——资产图通过 handout 系统分发，不通过 markdown
const renderer = new marked.Renderer();
renderer.image = () => "";
marked.setOptions({ breaks: true, gfm: true, renderer });

// ---- 流式输出目标 ----
let streamTarget: HTMLElement | null = null;

export function getStreamTarget(): HTMLElement | null {
  return streamTarget;
}

export function setStreamTarget(el: HTMLElement | null) {
  streamTarget = el;
}

// ---- 消息 ID 计数器 ----
let msgIdCounter = 0;

// ---- 添加消息（完整 Markdown 解析） ----
export function addMsg(kind: string, text: string): HTMLElement {
  const el = document.createElement("div");
  el.className = `msg ${kind}`;
  el.id = `msg-${++msgIdCounter}`;
  el.innerHTML = marked.parse(text) as string;
  messagesEl.appendChild(el);
  scrollDown();
  return el;
}

// ---- 滚动到底部 ----
export function scrollDown() {
  setTimeout(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }, 50);
}

// ---- 移除"守秘人思考中"指示 ----
export function removeLoading() {
  const dots = document.getElementById("loading-dots");
  if (dots) dots.remove();
}

// ---- 显示"守秘人思考中" ----
export function showGmThinking() {
  removeLoading();
  const dots = document.createElement("div");
  dots.className = "msg system";
  dots.id = "loading-dots";
  dots.innerHTML =
    '<div class="typing-dots"><span></span><span></span><span></span></div><span style="margin-left:8px;font-size:13px">守秘人正在叙述……</span>';
  messagesEl.appendChild(dots);
  scrollDown();
}

// ---- 流式文本缓冲 ----
let streamBuffer = "";

// ---- 流式文本到达 ----
export function onNarrativeChunk(text: string) {
  removeLoading();
  if (!streamTarget || streamTarget.className !== "msg gm streaming-cursor") {
    streamTarget = addMsg("gm", "");
    streamTarget.classList.add("streaming-cursor");
    streamBuffer = "";
  }
  streamBuffer += text;
  streamTarget.innerHTML = marked.parse(streamBuffer) as string;
  scrollDown();
}

// ---- 紧张感提示 ----
export function onTension(text: string) {
  addMsg("tension", text);
  showGmThinking();
}

// ---- 骰子结果 ----
export function onDice(text: string) {
  removeLoading();
  if (streamTarget) {
    streamTarget.classList.remove("streaming-cursor");
    streamTarget = null;
    streamBuffer = "";
  }
  addMsg("dice", text);
}

// ---- GM 摘要 ----
export function onSummary(text: string) {
  addMsg("summary", text);
}
