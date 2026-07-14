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

type DiceRollData = {
  d100_roll?: number;
  tens_dice?: number[];
  ones_dice?: number;
  bonus_dice?: number;
  penalty_dice?: number;
  spec?: string;
  sides?: number;
  rolls?: number[];
  total?: number;
};

type VisualDie = {
  min: number;
  max: number;
  final: number;
  label: string;
  formatter?: (value: number) => string;
};

// ---- 添加消息（完整 Markdown 解析） ----
export function addMsg(kind: string, text: string, forceScroll = false): HTMLElement {
  const el = document.createElement("div");
  el.className = `msg ${kind}`;
  el.id = `msg-${++msgIdCounter}`;
  el.innerHTML = marked.parse(text) as string;
  messagesEl.appendChild(el);
  scrollDown(forceScroll);  // forceScroll=true 强制滚；否则按 pinnedToBottom（fire 时复检）
  return el;
}

// ---- 自动滚动：用户是否"钉"在底部 ----
// 用滚动事件驱动的 sticky 状态，而非每次 chunk 到达时才查位置。后者会被
// 平滑滚动动画干扰：用户往上滚时 scrollTop 可能仍接近底部，被判为"在底部"
// 又被拉回去。sticky 状态在用户滚动当下就更新，流式输出期间不再抢滚动。
let pinnedToBottom = true;

messagesEl.addEventListener("scroll", () => {
  pinnedToBottom = isNearBottom();
}, { passive: true });

function isNearBottom(): boolean {
  const distance = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
  // 阈值要小：用户上滚一档(>50px)就应判"离底"停跟。原来 96px 太大——
  // 每个 chunk 把你 reset 到 0，你永远攒不到 96px，于是被反复拉回(抽搐几下)。
  return distance < 8;
}

// ---- 滚动到底部 ----
export function scrollDown(force = false) {
  setTimeout(() => {
    if (!force && !pinnedToBottom) return;
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

export function showRollPending() {
  removeLoading();
  if (document.getElementById("roll-pending")) return;
  const el = document.createElement("div");
  el.className = "msg dice roll-pending rolling";
  el.id = "roll-pending";
  el.innerHTML = `
    <div class="dice-title">判定中</div>
    <div class="dice-stage">
      <div class="dice-wrap">
        <div class="dice-face"><span>?</span></div>
        <div class="dice-face-label">等待结果</div>
      </div>
    </div>
    <div class="dice-result">守秘人正在结算检定……</div>
  `;
  messagesEl.appendChild(el);
  scrollDown();
}

export function removeRollPending() {
  const el = document.getElementById("roll-pending");
  if (el) el.remove();
}

// ---- 流式文本缓冲 ----
let streamBuffer = "";
let pendingStreamText = "";
let streamFrame: number | null = null;

export function flushNarrativeStream() {
  if (streamFrame !== null) {
    cancelAnimationFrame(streamFrame);
    streamFrame = null;
  }
  if (!pendingStreamText) return;

  const wasAtBottom = isNearBottom();
  removeLoading();
  if (!streamTarget || streamTarget.className !== "msg gm streaming-cursor") {
    streamTarget = addMsg("gm", "");
    streamTarget.classList.add("streaming-cursor");
    streamBuffer = "";
  }
  streamBuffer += pendingStreamText;
  pendingStreamText = "";
  streamTarget.innerHTML = marked.parse(streamBuffer) as string;
  if (wasAtBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ---- 流式文本到达 ----
export function onNarrativeChunk(text: string) {
  pendingStreamText += text;
  if (streamFrame === null) {
    streamFrame = requestAnimationFrame(() => {
      streamFrame = null;
      flushNarrativeStream();
    });
  }
}

// ---- 紧张感提示 ----
export function onTension(text: string) {
  flushNarrativeStream();
  addMsg("tension", text);
  showGmThinking();
}

// ---- 骰子结果 ----
export function onDice(text: string, rollData?: DiceRollData) {
  flushNarrativeStream();
  removeLoading();
  removeRollPending();
  if (streamTarget) {
    streamTarget.classList.remove("streaming-cursor");
    streamTarget = null;
    streamBuffer = "";
  }

  const dice = normalizeDiceVisual(rollData, text);
  if (dice.length === 0) {
    addMsg("dice", text);
    return;
  }

  const el = document.createElement("div");
  el.className = "msg dice rolling";
  el.id = `msg-${++msgIdCounter}`;

  const title = document.createElement("div");
  title.className = "dice-title";
  title.textContent = "命运之骰翻滚";
  el.appendChild(title);

  const stage = document.createElement("div");
  stage.className = "dice-stage";
  const valueEls: HTMLSpanElement[] = [];
  dice.forEach((die) => {
    const wrap = document.createElement("div");
    wrap.className = "dice-wrap";

    const face = document.createElement("div");
    face.className = "dice-face";
    face.dataset.sides = String(die.max);

    const value = document.createElement("span");
    value.textContent = formatDieValue(die, randomInt(die.min, die.max));
    face.appendChild(value);
    valueEls.push(value);

    const label = document.createElement("div");
    label.className = "dice-face-label";
    label.textContent = die.label;

    wrap.appendChild(face);
    wrap.appendChild(label);
    stage.appendChild(wrap);
  });
  el.appendChild(stage);

  const result = document.createElement("div");
  result.className = "dice-result hidden";
  result.setAttribute("aria-live", "polite");
  result.textContent = text;
  el.appendChild(result);

  messagesEl.appendChild(el);
  scrollDown();
  animateDice(el, dice, valueEls, result);
}

function normalizeDiceVisual(data: DiceRollData | undefined, summary: string): VisualDie[] {
  if (data?.d100_roll !== undefined) {
    const roll = Number(data.d100_roll);
    const fallbackTens = roll === 100 ? 0 : Math.floor(roll / 10);
    const fallbackOnes = roll === 100 ? 0 : roll % 10;
    const tensDice = Array.isArray(data.tens_dice) && data.tens_dice.length > 0
      ? data.tens_dice
      : [fallbackTens];
    const tenFormatter = (value: number) => (value === 0 ? "00" : String(value * 10));
    const dice: VisualDie[] = tensDice.map((value, index) => ({
      min: 0,
      max: 9,
      final: clampInt(value, 0, 9),
      label: index === 0 ? "十位" : extraTensLabel(data),
      formatter: tenFormatter,
    }));
    dice.push({
      min: 0,
      max: 9,
      final: clampInt(data.ones_dice ?? fallbackOnes, 0, 9),
      label: "个位",
    });
    return dice;
  }

  if (Array.isArray(data?.rolls) && data.rolls.length > 0) {
    const sides = Math.max(2, Number(data.sides) || parseSides(data.spec) || 20);
    return data.rolls.map((roll) => ({
      min: 1,
      max: sides,
      final: clampInt(roll, 1, sides),
      label: `d${sides}`,
    }));
  }

  const d100Match = summary.match(/d100\s*=\s*(\d+)/i);
  if (d100Match) {
    return [{ min: 1, max: 100, final: clampInt(Number(d100Match[1]), 1, 100), label: "d100" }];
  }

  const specMatch = summary.match(/\bD?(\d+)\s*=\s*(\d+)/i);
  if (specMatch) {
    const sides = Math.max(2, Number(specMatch[1]));
    return [{ min: 1, max: sides, final: clampInt(Number(specMatch[2]), 1, sides), label: `d${sides}` }];
  }

  return [];
}

function extraTensLabel(data: DiceRollData | undefined): string {
  if ((data?.bonus_dice || 0) > 0) return "奖励";
  if ((data?.penalty_dice || 0) > 0) return "惩罚";
  return "加骰";
}

function parseSides(spec?: string): number | null {
  const match = spec?.match(/d(\d+)/i);
  return match ? Number(match[1]) : null;
}

function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.round(value)));
}

function randomInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function formatDieValue(die: VisualDie, value: number): string {
  return die.formatter ? die.formatter(value) : String(value);
}

function animateDice(el: HTMLElement, dice: VisualDie[], valueEls: HTMLSpanElement[], resultEl: HTMLElement) {
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const timers: number[] = [];

  if (reduceMotion) {
    dice.forEach((die, index) => {
      valueEls[index].textContent = formatDieValue(die, die.final);
    });
    el.classList.remove("rolling");
    el.classList.add("settled");
    resultEl.classList.remove("hidden");
    scrollDown();
    return;
  }

  dice.forEach((die, index) => {
    const timer = window.setInterval(() => {
      valueEls[index].textContent = formatDieValue(die, randomInt(die.min, die.max));
    }, 48);
    timers.push(timer);

    window.setTimeout(() => {
      window.clearInterval(timer);
      valueEls[index].textContent = formatDieValue(die, die.final);
      valueEls[index].parentElement?.classList.add("locked");
    }, 440 + index * 80);
  });

  window.setTimeout(() => {
    timers.forEach((timer) => window.clearInterval(timer));
    dice.forEach((die, index) => {
      valueEls[index].textContent = formatDieValue(die, die.final);
      valueEls[index].parentElement?.classList.add("locked");
    });
    el.classList.remove("rolling");
    el.classList.add("settled");
    resultEl.classList.remove("hidden");
    scrollDown();
  }, 560 + dice.length * 80);
}

// ---- GM 摘要 ----
export function onSummary(text: string) {
  flushNarrativeStream();
  addMsg("summary", text);
}
