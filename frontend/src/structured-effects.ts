/**
 * structured-effects.ts — 结构化事件 → 现有 UI 的投影。
 *
 * 结构化事件按**明确 speaker** 渲染，不解析正文猜发言人；位置只由
 * `scene_changed` 更新（复用 scene-store）；骰子动画只用服务端结果，
 * 动画失败由既有 DiceMessage 回退成文字，不会重掷。
 *
 * 这一层不做任何业务判断：不推断成败、不补发未收到的事实。
 */

import { showHandout, updateCharPanel } from "./panels";
import { onDice, onNarrativeChunk, onNarrativeSegment } from "./renderer";
import type { DiceRollData } from "./renderer";
import type { Speaker } from "./state/message-store";
import {
  STRUCTURED_EVENT_TYPES,
  readInteractionThread,
} from "./protocol/structured";
import { useAppStore } from "./state/app-store";
import { useOnlineStore } from "./state/online-store";
import { useSceneStore } from "./state/scene-store";
import { useStructuredStore } from "./state/structured-store";
import { useStartStore } from "./state/start-store";
import {
  SPEAKER_KINDS,
  type StructuredEventEnvelope,
} from "./protocol/structured";

/**
 * 已渲染正文的消息标记（按 world + message_id）。
 * 同一世界内不重复渲染；世界切换后旧键不再命中，新世界的同 ID 消息正常工作。
 */
const renderedMessageBodies = new Set<string>();

function messageKey(
  envelope: StructuredEventEnvelope,
  payload: Record<string, unknown>,
): string {
  const messageId = text(payload.message_id) || text(payload.id);
  if (messageId) return `${envelope.world_id}:${messageId}`;
  return `${envelope.world_id}:seq-${envelope.sequence ?? envelope.event_id}`;
}

function messageBodySeen(
  envelope: StructuredEventEnvelope,
  payload: Record<string, unknown>,
): boolean {
  return renderedMessageBodies.has(messageKey(envelope, payload));
}

function markMessageBody(
  envelope: StructuredEventEnvelope,
  payload: Record<string, unknown>,
): void {
  if (renderedMessageBodies.size > 512) renderedMessageBodies.clear();
  renderedMessageBodies.add(messageKey(envelope, payload));
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * M0 `speaker` 形态是 `{kind, id?}`（见 schemas/structured-play/v1/common.json）。
 * 显示名优先用服务端投影的 `name`；没有名字时按 kind 给一个中性称谓，
 * **绝不**去正文里猜发言人。
 */
function readSpeaker(value: unknown): Speaker | undefined {
  if (!value || typeof value !== "object") return undefined;
  const record = value as Record<string, unknown>;
  const kind = text(record.kind);
  if (!(SPEAKER_KINDS as readonly string[]).includes(kind)) return undefined;
  const fallbackName =
    kind === "keeper" ? "守秘人" : kind === "system" ? "系统" : "";
  const name = text(record.name) || fallbackName;
  if (!name) return undefined;
  const avatar =
    record.avatar && typeof record.avatar === "object"
      ? (record.avatar as Speaker["avatar"])
      : undefined;
  return {
    type: kind as Speaker["type"],
    id: text(record.id) || undefined,
    name,
    ...(avatar ? { avatar } : {}),
  };
}

/** `roll_resolved` / `check_resolved` 的骰子可视化数据（全部来自服务端）。 */
export function rollVisualFromPayload(
  payload: Record<string, unknown>,
): DiceRollData | null {
  const dice = Array.isArray(payload.dice) ? payload.dice : [];
  const first = dice[0] as Record<string, unknown> | undefined;
  const sides = first ? numeric(first.sides) : null;
  const values = Array.isArray(first?.values)
    ? (first?.values as unknown[]).flatMap((value) => {
        const n = numeric(value);
        return n === null ? [] : [n];
      })
    : [];
  const total =
    numeric(payload.total) ??
    numeric(payload.roll) ??
    (values.length ? values[0] : null);
  if (total === null) return null;
  if (sides === 100) {
    // d100 用十位/个位两张骰面，沿用既有动画的分解方式。
    const rolled = values[0] ?? total;
    return {
      d100_roll: rolled,
      tens_dice: [rolled === 100 ? 0 : Math.floor(rolled / 10)],
      ones_dice: rolled === 100 ? 0 : rolled % 10,
      total,
      spec: text(payload.expression) || "1d100",
    };
  }
  return {
    rolls: values.length ? values : [total],
    sides: sides ?? 20,
    total,
    spec: text(payload.expression) || `1d${sides ?? 20}`,
  };
}

function renderRoll(payload: Record<string, unknown>, summary: string): void {
  const visual = rollVisualFromPayload(payload);
  // 只播服务端给的结果；没有可解析的骰面时按纯文字显示，不本地重掷。
  onDice(summary, visual ?? undefined);
}

/**
 * 复用场景指示器：快照恢复当前位置，scene_changed 更新位置；
 * 两者都来自服务端已提交状态，正文与关键词都不参与。
 */
function projectScene(
  envelope: StructuredEventEnvelope,
  payload: Record<string, unknown>,
): void {
  const scene = payload.scene as Record<string, unknown> | undefined;
  const name = text(scene?.name);
  if (name) {
    useSceneStore.getState().applyScene(envelope.world_id, { name });
  }
}

/**
 * 结构化世界进入“游戏中”：不需要旧回合的 gm_turn_start。
 * 打开输入框（玩家可以随时提交请求），并让顶栏位置行/工具行按开局态显示。
 */
function enterStructuredSession(): void {
  // 房间还在大厅/准备中时不要把界面推进“游戏中”：快照可能只是能力协商，
  // 真正的开局由房间状态（或旧回合的 gm_turn_start）决定。
  const app = useAppStore.getState();
  const roomStatus = useOnlineStore.getState().roomStatus ?? "";
  const inLobby =
    app.mode === "online" && ["lobby", "starting"].includes(roomStatus);
  if (inLobby) return;

  const start = useStartStore.getState();
  if (!start.gameStarted) {
    useStartStore.setState({ gameStarted: true, gameStarting: false });
  }
  if (!app.inputEnabled) {
    app.setInput(true, "你决定做什么？");
  }
}

function applyStateChanged(payload: Record<string, unknown>): void {
  const character = useAppStore.getState().character;
  if (!character) return;
  const next = { ...character };
  const hp = numeric(payload.hp);
  const maxHp = numeric(payload.max_hp);
  const san = numeric(payload.san);
  const maxSan = numeric(payload.max_san);
  if (hp !== null) next.hp = hp;
  if (maxHp !== null) next.max_hp = maxHp;
  if (san !== null) next.san = san;
  if (maxSan !== null) next.max_san = maxSan;
  if (Array.isArray(payload.conditions)) {
    next.conditions = payload.conditions.filter(
      (item): item is string => typeof item === "string",
    );
  }
  useAppStore.getState().setCharacter(next);
}

/**
 * 应用一条已通过游标校验的结构化事件对现有 UI 的投影。
 * 调用方（structured-transport）负责去重与 store 更新。
 */
export function applyStructuredEffects(
  envelope: StructuredEventEnvelope,
): void {
  const payload = envelope.payload ?? {};
  switch (envelope.type) {
    case "session_snapshot": {
      // 结构化世界的“在游戏中/可提交”由服务端快照决定，而不是旧回合的
      // gm_turn_start/done：human 主持是异步待办，没有严格的行action顺序。
      enterStructuredSession();
      projectScene(envelope, payload);
      break;
    }
    case "scene_changed": {
      projectScene(envelope, payload);
      break;
    }
    case "message_started": {
      onNarrativeSegment(readSpeaker(payload.speaker));
      break;
    }
    case "message_chunk": {
      const speaker = readSpeaker(payload.speaker);
      if (speaker) onNarrativeSegment(speaker);
      const body = text(payload.text);
      if (body) {
        markMessageBody(envelope, payload);
        onNarrativeChunk(body, speaker?.type === "npc" ? speaker.id : null);
      }
      break;
    }
    case "message_completed": {
      const speaker = readSpeaker(payload.speaker);
      if (speaker) onNarrativeSegment(speaker);
      // 短发言可能只有定稿没有分片：这时必须补渲染正文，否则消息会凭空消失。
      // 已经流式渲染过的消息不再重复追加（按 world + message_id 记忆）。
      const body = text(payload.text);
      if (body && !messageBodySeen(envelope, payload)) {
        onNarrativeChunk(body, speaker?.type === "npc" ? speaker.id : null);
      }
      markMessageBody(envelope, payload);
      break;
    }
    case "roll_resolved": {
      const spec = text(payload.expression) || "1d100";
      const total = numeric(payload.total) ?? numeric(payload.roll);
      renderRoll(
        payload,
        `普通掷骰 ${spec} → ${total ?? "?"}（本次掷骰不触发剧情效果）`,
      );
      break;
    }
    case "check_resolved": {
      const skill = text(payload.skill) || "检定";
      const roll = numeric(payload.roll);
      const target = numeric(payload.target_value);
      const outcome = text(payload.outcome);
      renderRoll(
        payload,
        `${skill}检定 ${roll ?? "?"} vs ${target ?? "?"}${
          outcome ? `，${outcome === "success" ? "成功" : "失败"}` : ""
        }`,
      );
      break;
    }
    case "handout_presented": {
      // 主持展示素材：复用既有 handouts 展示链，不新增第二套图片出口。
      const assetId = text(payload.asset_id);
      const caption = text(payload.caption);
      if (!assetId) break;
      showHandout({
        // 素材 ID 走 file 字段（既有展示链按 file/label/asset_* 渲染）。
        file: text(payload.file) || assetId,
        label: caption || text(payload.label),
        asset_data_uri: text(payload.asset_data_uri),
        asset_url: text(payload.asset_url),
        entity_type: text(payload.entity_type),
        entity_id: text(payload.entity_id),
      });
      break;
    }
    case "state_changed": {
      applyStateChanged(payload);
      break;
    }
    case "interaction_updated": {
      // M5：交互线程（已讨论目标/尚未执行行动/已告知）——记录而非执行授权，
      // 只用于展示；是否出发仍由主持命令决定。
      const thread = readInteractionThread(payload);
      if (thread) useStructuredStore.getState().upsertInteraction(thread);
      break;
    }
    case "memory_query_result": {
      // M5：主持侧只读查询结果（服务端 keeper 定向；玩家连接收不到）
      useStructuredStore.getState().applyMemoryQueryResult(payload);
      break;
    }
    case "memory_recorded": {
      // M5：主持显式记录了一条角色记忆（keeper 定向）。玩家视图不展示，
      // 主持台只用它提示“已记录”，不把记忆内容当权威世界状态。
      break;
    }
    default: {
      // 服务端可能先落地新事件（例如上下文/记忆改造的 interaction_updated）。
      // 前端解析器容错保留，但**不能静默丢弃**：记录类型并在待办区明确提示，
      // 载荷语义以冻结后的协议为准，这里不猜。
      const eventType = String(envelope.type || "");
      if (
        eventType &&
        !(STRUCTURED_EVENT_TYPES as readonly string[]).includes(eventType)
      ) {
        useStructuredStore.getState().noteUnknownEventType(eventType);
      }
      break;
    }
  }
}

/** 主持私聊/发言等会话结束后，面板数据仍需刷新时使用（保持与旧链路一致）。 */
export function refreshCharacterPanel(): void {
  const character = useAppStore.getState().character;
  if (character) updateCharPanel(JSON.stringify(character));
}
