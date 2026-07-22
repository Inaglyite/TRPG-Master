/**
 * React-facing message state adapter.
 *
 * WebSocket handlers call these deterministic commands; MessageList owns DOM.
 */

import {
  useMessageStore,
  type ChatMessage,
  type NarrativeSegment,
  type Speaker,
  type VisualDie,
} from "./state/message-store";

let messageCounter = 0;
let displayTurnId: string | null = null;
let streamMessageId: string | null = null;
let streamBuffer = "";
let playbackTimer: number | null = null;
let networkStreamFinished = false;
let authoritativeSegments: NarrativeSegment[] | null = null;
let choiceFastMode = false;
const presentationCallbacks: Array<() => void> = [];
const visibilityCallbacks: Array<() => void> = [];
let replacement: { sourceTurnId: string; targetId: string } | null = null;
const rewriteCallbacks = new Map<string, () => void>();
const branchCallbacks = new Map<string, () => void>();

// ---- 发言者段状态（流式期间实时构建，finalize 由权威段覆盖）----
let streamSegments: NarrativeSegment[] = [];
let streamNpc: string | null = null;
type PlaybackPiece = { chars: string[]; npcId: string | null; fast?: boolean };
const playbackQueue: PlaybackPiece[] = [];
const liveSpeakers = new Map<string, Speaker>();

const BASE_TICK_MS = 34;
const COMMA_PAUSE_MS = 72;
const SENTENCE_PAUSE_MS = 185;
const SPEAKER_PAUSE_MS = 240;

export type TurnHistoryItem = {
  turn_id?: string;
  parent_turn_id?: string | null;
  player_input?: string | null;
  narrative?: string;
  narrative_segments?: NarrativeSegment[];
  chat_events?: NarrativeSegment[];
  choices?: Array<{ label: string; isFree: boolean }>;
};

export function branchSourceTurnId(turn: TurnHistoryItem): string {
  return String(turn.parent_turn_id || turn.turn_id || "");
}

export type DiceRollData = {
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

function nextId() {
  return `msg-${++messageCounter}`;
}

function normalizeNarrativeSegment(
  segment: NarrativeSegment,
  fallbackEventId: string,
): NarrativeSegment {
  return {
    kind: segment.kind,
    text: String(segment.text || ""),
    eventId: segment.eventId || segment.event_id || fallbackEventId,
    ...(segment.npcId || segment.npc_id
      ? { npcId: segment.npcId || segment.npc_id }
      : {}),
    ...(segment.speaker ? { speaker: segment.speaker } : {}),
  };
}

function updateMessages(updater: (messages: ChatMessage[]) => ChatMessage[]) {
  useMessageStore.getState().updateMessages(updater);
}

function append(message: ChatMessage, forceScroll = false) {
  updateMessages((messages) => [...messages, message]);
  useMessageStore.getState().requestScroll(forceScroll);
}

function lastMessageIndex(
  messages: ChatMessage[],
  predicate: (message: ChatMessage) => boolean,
) {
  for (let index = messages.length - 1; index >= 0; index--) {
    if (predicate(messages[index])) return index;
  }
  return -1;
}

export function setDisplayTurnId(turnId: string | null) {
  displayTurnId = turnId;
}

export function removeTurnMessages(turnId: string) {
  updateMessages((messages) =>
    messages.filter((message) => message.turnId !== turnId),
  );
  if (
    streamMessageId &&
    !useMessageStore
      .getState()
      .messages.some((message) => message.id === streamMessageId)
  ) {
    clearStream();
  }
}

export function tagPendingPlayerMessage(turnId: string) {
  updateMessages((messages) => {
    const index = lastMessageIndex(
      messages,
      (message) => message.kind === "player" && !message.turnId,
    );
    if (index < 0) return messages;
    return messages.map((message, current) =>
      current === index ? { ...message, turnId } : message,
    );
  });
}

export function beginNarrativeReplacement(sourceTurnId: string) {
  flushNarrativeStream();
  const targetId = nextId();
  updateMessages((messages) => {
    const sourceIndices = messages
      .map((message, index) =>
        message.turnId === sourceTurnId && message.kind === "gm" ? index : -1,
      )
      .filter((index) => index >= 0);
    const insertion = sourceIndices.length
      ? sourceIndices[sourceIndices.length - 1]
      : messages.length;
    const hidden = messages.map((message) =>
      message.turnId === sourceTurnId && message.kind === "gm"
        ? { ...message, hidden: true }
        : message,
    );
    const target: ChatMessage = {
      id: targetId,
      kind: "gm",
      text: "",
      turnId: displayTurnId || undefined,
      streaming: true,
      rewriteTarget: true,
    };
    return [...hidden.slice(0, insertion), target, ...hidden.slice(insertion)];
  });
  streamMessageId = targetId;
  streamBuffer = "";
  playbackQueue.length = 0;
  streamSegments = [];
  streamNpc = null;
  networkStreamFinished = false;
  authoritativeSegments = null;
  choiceFastMode = false;
  replacement = { sourceTurnId, targetId };
  scrollDown(true);
}

export function completeNarrativeReplacement(sourceTurnId: string) {
  flushNarrativeStream();
  if (!replacement || replacement.sourceTurnId !== sourceTurnId) return;
  const targetId = replacement.targetId;
  updateMessages((messages) =>
    messages
      .filter(
        (message) =>
          !(
            message.turnId === sourceTurnId &&
            message.kind === "gm" &&
            message.id !== targetId
          ),
      )
      .map((message) =>
        message.id === targetId
          ? {
              ...message,
              turnId: sourceTurnId,
              streaming: false,
              rewriteTarget: false,
            }
          : message,
      ),
  );
  clearStream();
  replacement = null;
}

export function cancelNarrativeReplacement(sourceTurnId: string) {
  if (!replacement || replacement.sourceTurnId !== sourceTurnId) return;
  const targetId = replacement.targetId;
  updateMessages((messages) =>
    messages
      .filter((message) => message.id !== targetId)
      .map((message) =>
        message.turnId === sourceTurnId
          ? { ...message, hidden: false }
          : message,
      ),
  );
  clearStream();
  replacement = null;
  removeLoading();
}

export function attachTurnRewriteAction(turnId: string, onRewrite: () => void) {
  rewriteCallbacks.clear();
  rewriteCallbacks.set(turnId, onRewrite);
  updateMessages((messages) => {
    const target = lastMessageIndex(
      messages,
      (message) => message.turnId === turnId && message.kind === "gm",
    );
    return messages.map((message, index) => ({
      ...message,
      canRewrite: index === target,
    }));
  });
}

export function attachTurnBranchAction(turnId: string, onBranch: () => void) {
  branchCallbacks.set(turnId, onBranch);
  updateMessages((messages) => {
    const target = lastMessageIndex(
      messages,
      (message) => message.turnId === turnId && message.kind === "gm",
    );
    return messages.map((message, index) =>
      index === target ? { ...message, canBranch: true } : message,
    );
  });
}

export function invokeTurnRewrite(turnId: string) {
  rewriteCallbacks.get(turnId)?.();
}

export function invokeTurnBranch(turnId: string) {
  branchCallbacks.get(turnId)?.();
}

export function resetTurnActionButtons() {
  useMessageStore.getState().resetActionButtons();
}

export function renderTurnHistory(
  history: TurnHistoryItem[],
): TurnHistoryItem | null {
  clearStream();
  replacement = null;
  displayTurnId = null;
  rewriteCallbacks.clear();
  branchCallbacks.clear();
  const messages: ChatMessage[] = [];
  history.forEach((turn) => {
    const turnId = String(turn.turn_id || "");
    if (!turnId) return;
    if (turn.player_input) {
      messages.push({
        id: nextId(),
        kind: "player",
        text: String(turn.player_input),
        turnId,
      });
    }
    if (turn.narrative) {
      const storedEvents = turn.chat_events || turn.narrative_segments || [];
      const segments = storedEvents.length
        ? storedEvents.map((segment, segmentIndex) =>
            normalizeNarrativeSegment(
              segment,
              `turn-${turnId}-${segmentIndex}`,
            ),
          )
        : [
            normalizeNarrativeSegment(
              { kind: "narration", text: String(turn.narrative) },
              `turn-${turnId}-legacy`,
            ),
          ];
      messages.push({
        id: nextId(),
        kind: "gm",
        text: String(turn.narrative),
        turnId,
        segments,
      });
    }
  });
  useMessageStore.getState().replaceMessages(messages);
  scrollDown(true);
  return history.length ? history[history.length - 1] : null;
}

export function addMsg(
  kind: string,
  text: string,
  forceScroll = false,
): string {
  const id = nextId();
  append({ id, kind, text, turnId: displayTurnId || undefined }, forceScroll);
  return id;
}

export function scrollDown(force = false) {
  useMessageStore.getState().requestScroll(force);
}

export function removeLoading() {
  updateMessages((messages) =>
    messages.filter((message) => message.kind !== "loading"),
  );
}

export function showGmThinking(label = "守秘人正在叙述……") {
  removeLoading();
  append({
    id: nextId(),
    kind: "loading",
    text: label,
    turnId: displayTurnId || undefined,
  });
}

export function showRollPending() {
  removeLoading();
  if (
    useMessageStore
      .getState()
      .messages.some((message) => message.kind === "roll-pending")
  )
    return;
  append({
    id: "roll-pending",
    kind: "roll-pending",
    text: "守秘人正在结算检定……",
    turnId: displayTurnId || undefined,
  });
}

export function removeRollPending() {
  updateMessages((messages) =>
    messages.filter((message) => message.kind !== "roll-pending"),
  );
}

export function hasActiveNarrativeStream() {
  return streamMessageId !== null;
}

export function finishNarrativeStream() {
  networkStreamFinished = true;
  if (playbackQueue.length) {
    schedulePlayback();
    return;
  }
  finalizePresentedStream();
}

function finalizePresentedStream() {
  applyAuthoritativeSegments();
  if (!streamMessageId) {
    networkStreamFinished = false;
    authoritativeSegments = null;
    choiceFastMode = false;
    notifyPresentationComplete();
    return;
  }
  const targetId = streamMessageId;
  updateMessages((messages) =>
    messages.map((message) =>
      message.id === targetId ? { ...message, streaming: false } : message,
    ),
  );
  streamMessageId = null;
  streamBuffer = "";
  streamSegments = [];
  streamNpc = null;
  authoritativeSegments = null;
  networkStreamFinished = false;
  choiceFastMode = false;
  notifyPresentationComplete();
}

function clearStream() {
  if (playbackTimer !== null) {
    window.clearTimeout(playbackTimer);
    playbackTimer = null;
  }
  streamMessageId = null;
  streamBuffer = "";
  playbackQueue.length = 0;
  streamSegments = [];
  streamNpc = null;
  authoritativeSegments = null;
  networkStreamFinished = false;
  choiceFastMode = false;
  presentationCallbacks.splice(0);
  visibilityCallbacks.splice(0);
}

/** 把一段带发言者上下文的文本并入流式段结构。 */
function pushStreamPiece(text: string, npcId: string | null) {
  if (!streamSegments.length || npcId !== streamNpc) {
    streamNpc = npcId;
    const speaker = npcId ? liveSpeakers.get(npcId) : undefined;
    streamSegments.push({
      kind: npcId ? "speech" : "narration",
      text: "",
      eventId: `live-${displayTurnId || "turn"}-${streamSegments.length}`,
      ...(npcId ? { npcId } : {}),
      ...(speaker ? { speaker } : {}),
    });
  }
  const current = streamSegments[streamSegments.length - 1];
  current.text += text;
}

function ensureStreamMessage() {
  if (streamMessageId) return;
  streamMessageId = nextId();
  streamSegments = [];
  streamNpc = null;
  append({
    id: streamMessageId,
    kind: "gm",
    text: "",
    turnId: displayTurnId || undefined,
    streaming: true,
  });
  streamBuffer = "";
}

function renderPlaybackText(text: string, npcId: string | null) {
  if (!text) return;
  removeLoading();
  ensureStreamMessage();
  streamBuffer += text;
  pushStreamPiece(text, npcId);
  const targetId = streamMessageId!;
  const segments = streamSegments.map((segment) => ({ ...segment }));
  updateMessages((messages) =>
    messages.map((message) =>
      message.id === targetId
        ? { ...message, text: streamBuffer, segments }
        : message,
    ),
  );
  scrollDown();
  if (streamBuffer && visibilityCallbacks.length) {
    const callbacks = visibilityCallbacks.splice(0);
    callbacks.forEach((callback) => callback());
  }
}

function queuedCharacterCount() {
  return playbackQueue.reduce((total, piece) => total + piece.chars.length, 0);
}

function charactersPerTick(backlog: number, fast = false) {
  if (fast) return 6;
  if (backlog > 500) return 3;
  if (backlog > 160) return 2;
  return 1;
}

function playbackDelay(
  lastChar: string,
  speakerChanged: boolean,
  fast = false,
) {
  if (fast) return /[。！？!?]/u.test(lastChar) ? 46 : 18;
  if (speakerChanged) return SPEAKER_PAUSE_MS;
  if (/[。！？!?]/u.test(lastChar)) return SENTENCE_PAUSE_MS;
  if (/[,，、；;：:]/u.test(lastChar)) return COMMA_PAUSE_MS;
  if (lastChar === "\n") return 110;
  return BASE_TICK_MS;
}

function schedulePlayback(delay = BASE_TICK_MS) {
  if (playbackTimer !== null || !playbackQueue.length) return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    flushNarrativeStream();
    return;
  }
  playbackTimer = window.setTimeout(playbackStep, delay);
}

function playbackStep() {
  playbackTimer = null;
  const piece = playbackQueue[0];
  if (!piece) {
    if (networkStreamFinished) finalizePresentedStream();
    return;
  }
  const count = charactersPerTick(queuedCharacterCount(), piece.fast);
  const text = piece.chars.splice(0, count).join("");
  renderPlaybackText(text, piece.npcId);
  if (!piece.chars.length) playbackQueue.shift();
  const speakerChanged =
    Boolean(playbackQueue.length) && playbackQueue[0].npcId !== piece.npcId;
  if (playbackQueue.length) {
    schedulePlayback(
      playbackDelay(
        Array.from(text).at(-1) || "",
        speakerChanged,
        playbackQueue[0].fast,
      ),
    );
  } else if (networkStreamFinished) {
    finalizePresentedStream();
  }
}

export function flushNarrativeStream() {
  if (playbackTimer !== null) {
    window.clearTimeout(playbackTimer);
    playbackTimer = null;
  }
  while (playbackQueue.length) {
    const piece = playbackQueue.shift()!;
    renderPlaybackText(piece.chars.join(""), piece.npcId);
  }
  applyAuthoritativeSegments();
  if (networkStreamFinished) finalizePresentedStream();
}

export function onNarrativeChunk(text: string, npcId?: string | null) {
  if (!text) return;
  removeLoading();
  ensureStreamMessage();
  const markerIndex = choiceFastMode
    ? 0
    : text.search(/你可以|您可以|\n\s*1[.、]/u);
  if (markerIndex > 0) {
    playbackQueue.push({
      chars: Array.from(text.slice(0, markerIndex)),
      npcId: npcId || null,
    });
  }
  playbackQueue.push({
    chars: Array.from(markerIndex >= 0 ? text.slice(markerIndex) : text),
    npcId: npcId || null,
    fast: choiceFastMode || markerIndex >= 0,
  });
  if (markerIndex >= 0) choiceFastMode = true;
  else accelerateNarrativeChoices();
  schedulePlayback();
}

export function accelerateNarrativeChoices(force = false) {
  let tail = "";
  let choicesStarted = false;
  let foundMarker = false;
  for (const piece of playbackQueue) {
    const text = piece.chars.join("");
    if (!choicesStarted && /你可以|您可以|\n\s*1[.、]/u.test(tail + text)) {
      choicesStarted = true;
      foundMarker = true;
    }
    if (choicesStarted) piece.fast = true;
    tail = (tail + text).slice(-24);
  }
  if (!foundMarker && (choiceFastMode || force)) {
    playbackQueue.forEach((piece) => {
      piece.fast = true;
    });
  }
  choiceFastMode = choiceFastMode || foundMarker || force;
  if (choicesStarted && playbackTimer !== null) {
    window.clearTimeout(playbackTimer);
    playbackTimer = null;
    schedulePlayback(18);
  }
}

export function revealNarrativeImmediately() {
  flushNarrativeStream();
}

export function whenNarrativePresented(callback: () => void) {
  if (!playbackQueue.length && !streamMessageId) {
    callback();
    return;
  }
  presentationCallbacks.push(callback);
}

export function whenNarrativeVisible(callback: () => void) {
  if (streamBuffer) {
    callback();
    return;
  }
  visibilityCallbacks.push(callback);
}

function notifyPresentationComplete() {
  const callbacks = presentationCallbacks.splice(0);
  callbacks.forEach((callback) => callback());
}

/** 流式期间的 NPC 发言段开始：记录发言者资料，供段渲染取用。 */
export function onNarrativeSegment(speaker: Speaker | undefined) {
  if (!speaker?.id) return;
  liveSpeakers.set(speaker.id, speaker);

  // WebSocket 事件有序，但浏览器会按 animation frame 批量提交文本。若发言文本
  // 已先形成占位段，身份资料到达后应立即回填，而不是等整个回合定稿。
  let changed = false;
  streamSegments = streamSegments.map((segment) => {
    if (segment.kind !== "speech" || segment.npcId !== speaker.id)
      return segment;
    changed = true;
    return { ...segment, speaker };
  });
  if (!changed || !streamMessageId) return;
  const targetId = streamMessageId;
  const segments = streamSegments.map((segment) => ({ ...segment }));
  updateMessages((messages) =>
    messages.map((message) =>
      message.id === targetId ? { ...message, segments } : message,
    ),
  );
}

/** 回合定稿：以服务端权威段结构覆盖流式期间的实时段。 */
export function onNarrativeSegments(segments: NarrativeSegment[]) {
  if (!Array.isArray(segments) || !segments.length) return;
  authoritativeSegments = segments.map((segment, segmentIndex) =>
    normalizeNarrativeSegment(
      segment,
      `turn-${displayTurnId || "unknown"}-${segmentIndex}`,
    ),
  );
  if (streamMessageId && playbackQueue.length) {
    rebasePlaybackOnAuthoritativeSegments(authoritativeSegments);
  } else if (!playbackQueue.length) {
    applyAuthoritativeSegments();
  }
}

/**
 * The provider may return one large untagged chunk.  Speaker inference then
 * completes while the presentation queue is still typing.  Rebase both the
 * visible prefix and the unplayed suffix onto the authoritative speaker map so
 * bubbles change during playback instead of jumping only at turn completion.
 */
function rebasePlaybackOnAuthoritativeSegments(segments: NarrativeSegment[]) {
  const consumedChars = Array.from(streamBuffer).length;
  let remainingConsumed = consumedChars;
  const presented: NarrativeSegment[] = [];
  const queued: PlaybackPiece[] = [];

  for (const segment of segments) {
    const chars = Array.from(segment.text);
    const consumedHere = Math.min(remainingConsumed, chars.length);
    if (consumedHere > 0) {
      presented.push({
        ...segment,
        text: chars.slice(0, consumedHere).join(""),
      });
      remainingConsumed -= consumedHere;
    }
    const rest = chars.slice(consumedHere);
    if (rest.length) {
      const npcId =
        segment.kind === "speech"
          ? segment.npcId || segment.npc_id || segment.speaker?.id || null
          : null;
      if (npcId && segment.speaker) liveSpeakers.set(npcId, segment.speaker);
      queued.push({ chars: rest, npcId, fast: choiceFastMode });
    }
  }

  streamSegments = presented;
  streamNpc =
    presented.at(-1)?.kind === "speech"
      ? presented.at(-1)?.npcId || presented.at(-1)?.npc_id || null
      : null;
  playbackQueue.splice(0, playbackQueue.length, ...queued);
  const targetId = streamMessageId;
  updateMessages((messages) =>
    messages.map((message) =>
      message.id === targetId
        ? { ...message, segments: presented.map((segment) => ({ ...segment })) }
        : message,
    ),
  );
  authoritativeSegments = null;
  accelerateNarrativeChoices(choiceFastMode);
}

function applyAuthoritativeSegments() {
  if (!authoritativeSegments?.length) return;
  updateMessages((messages) => {
    const index = lastMessageIndex(
      messages,
      (message) =>
        message.kind === "gm" &&
        (!displayTurnId || message.turnId === displayTurnId),
    );
    if (index < 0) return messages;
    const authoritative = authoritativeSegments!.map((segment) => ({
      ...segment,
    }));
    return messages.map((message, current) =>
      current === index ? { ...message, segments: authoritative } : message,
    );
  });
  authoritativeSegments = null;
}

export function onTension(text: string) {
  flushNarrativeStream();
  addMsg("tension", text);
  showGmThinking();
}

export function onDice(text: string, rollData?: DiceRollData) {
  flushNarrativeStream();
  removeLoading();
  removeRollPending();
  finishNarrativeStream();
  append({
    id: nextId(),
    kind: "dice",
    text,
    turnId: displayTurnId || undefined,
    dice: normalizeDiceVisual(rollData, text),
  });
}

function normalizeDiceVisual(
  data: DiceRollData | undefined,
  summary: string,
): VisualDie[] {
  if (data?.d100_roll !== undefined) {
    const roll = Number(data.d100_roll);
    const fallbackTens = roll === 100 ? 0 : Math.floor(roll / 10);
    const fallbackOnes = roll === 100 ? 0 : roll % 10;
    const tensDice =
      Array.isArray(data.tens_dice) && data.tens_dice.length
        ? data.tens_dice
        : [fallbackTens];
    const dice: VisualDie[] = tensDice.map((value, index) => ({
      min: 0,
      max: 9,
      final: clampInt(value, 0, 9),
      label: index === 0 ? "十位" : extraTensLabel(data),
      formatter: "tens",
    }));
    dice.push({
      min: 0,
      max: 9,
      final: clampInt(data.ones_dice ?? fallbackOnes, 0, 9),
      label: "个位",
    });
    return dice;
  }
  if (Array.isArray(data?.rolls) && data.rolls.length) {
    const sides = Math.max(
      2,
      Number(data.sides) || parseSides(data.spec) || 20,
    );
    return data.rolls.map((roll) => ({
      min: 1,
      max: sides,
      final: clampInt(roll, 1, sides),
      label: `d${sides}`,
    }));
  }
  const d100 = summary.match(/d100\s*=\s*(\d+)/i);
  if (d100)
    return [
      {
        min: 1,
        max: 100,
        final: clampInt(Number(d100[1]), 1, 100),
        label: "d100",
      },
    ];
  const die = summary.match(/\bD?(\d+)\s*=\s*(\d+)/i);
  if (die) {
    const sides = Math.max(2, Number(die[1]));
    return [
      {
        min: 1,
        max: sides,
        final: clampInt(Number(die[2]), 1, sides),
        label: `d${sides}`,
      },
    ];
  }
  return [];
}

function extraTensLabel(data: DiceRollData | undefined) {
  if ((data?.bonus_dice || 0) > 0) return "奖励";
  if ((data?.penalty_dice || 0) > 0) return "惩罚";
  return "加骰";
}

function parseSides(spec?: string): number | null {
  const match = spec?.match(/d(\d+)/i);
  return match ? Number(match[1]) : null;
}

function clampInt(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.round(value)));
}

export function onSummary(text: string) {
  flushNarrativeStream();
  addMsg("summary", text);
}
