import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { applyStructuredEffects } from "../structured-effects";
import {
  onNarrativeChunk,
  hasActiveNarrativeStream,
  resetGamePresentation,
} from "../renderer";
import { useMessageStore } from "./message-store";
import { initialStructuredState, useStructuredStore } from "./structured-store";
import { useStartStore } from "./start-store";
import { useOnlineStore, initialOnlineState } from "./online-store";
import { useAppStore } from "./app-store";
import type { StructuredEventEnvelope } from "../protocol/structured";

vi.mock("../panels", () => ({
  showHandout: vi.fn(),
  updateCharPanel: vi.fn(),
}));

let sequence = 0;
function message(type: string, payload: Record<string, unknown>) {
  applyStructuredEffects({
    protocol_version: 1,
    event_id: ++sequence,
    sequence,
    revision: 1,
    world_id: "narrative-world",
    cause_request_id: null,
    type,
    payload,
  } as StructuredEventEnvelope);
}
function complete(
  id: string,
  text: string,
  speaker = { kind: "keeper", id: "" },
) {
  message("message_completed", { message_id: id, text, speaker });
}

beforeEach(() => {
  vi.useFakeTimers();
  resetGamePresentation();
  useAppStore.setState({ mode: "online", inputEnabled: false });
  useOnlineStore.setState({ ...initialOnlineState, roomStatus: "playing" });
  useStructuredStore.setState({
    ...initialStructuredState,
    identity: {
      ...initialStructuredState.identity,
      worldId: "narrative-world",
    },
    targets: [{ id: "doctor", kind: "npc", name: "惠特克罗夫医生" }],
  });
});
afterEach(() => {
  resetGamePresentation();
  vi.useRealTimers();
});

describe("structured narration uses message identity, not a global legacy stream", () => {
  it("consecutive completed messages from the same speaker remain separate and settled", () => {
    complete("first", "医生翻开病历。");
    complete("second", "窗外又响起雨声。");
    expect(useMessageStore.getState().messages).toMatchObject([
      {
        id: "structured:narrative-world:first",
        text: "医生翻开病历。",
        streaming: false,
      },
      {
        id: "structured:narrative-world:second",
        text: "窗外又响起雨声。",
        streaming: false,
      },
    ]);
    expect(hasActiveNarrativeStream()).toBe(false);
  });

  it("a chunk without speaker inherits its own start, not another speaker's global state", () => {
    message("message_started", {
      message_id: "npc",
      speaker: { kind: "npc", id: "doctor" },
    });
    message("message_started", {
      message_id: "keeper",
      speaker: { kind: "keeper" },
    });
    message("message_chunk", { message_id: "npc", index: 0, text: "请坐。" });
    message("message_chunk", {
      message_id: "keeper",
      index: 0,
      text: "他合上病历。",
    });
    expect(useMessageStore.getState().messages).toMatchObject([
      {
        text: "请坐。",
        streaming: true,
        segments: [
          { kind: "speech", speaker: { id: "doctor", name: "惠特克罗夫医生" } },
        ],
      },
      {
        text: "他合上病历。",
        streaming: true,
        segments: [{ kind: "narration", speaker: { type: "keeper" } }],
      },
    ]);
  });

  it("completion replaces provisional chunks with authoritative text and ends streaming", () => {
    message("message_started", {
      message_id: "edited",
      speaker: { kind: "npc", id: "doctor" },
    });
    message("message_chunk", {
      message_id: "edited",
      index: 0,
      text: "未定稿",
    });
    complete("edited", "最终的回答。", { kind: "npc", id: "doctor" });
    expect(useMessageStore.getState().messages).toMatchObject([
      {
        text: "最终的回答。",
        streaming: false,
        segments: [{ text: "最终的回答。" }],
      },
    ]);
    expect(useMessageStore.getState().messages).toHaveLength(1);
  });

  it("late starts, chunks and completions do not duplicate a completed message", () => {
    complete("settled", "已提交。");
    message("message_started", {
      message_id: "settled",
      speaker: { kind: "keeper" },
    });
    message("message_chunk", {
      message_id: "settled",
      index: 0,
      text: "已提交。",
    });
    complete("settled", "已提交。");
    expect(useMessageStore.getState().messages).toMatchObject([
      { text: "已提交。", streaming: false },
    ]);
    expect(useMessageStore.getState().messages).toHaveLength(1);
  });

  it("investigator and system speakers retain their own attribution, never a keeper bubble", () => {
    complete("pc", "医生，我想看看遗体。", {
      kind: "investigator",
      id: "alice",
    });
    complete("notice", "这只是系统通知。", { kind: "system", id: "" });
    expect(useMessageStore.getState().messages).toMatchObject([
      {
        kind: "player",
        speaker: { type: "investigator", id: "alice" },
        streaming: false,
      },
      { kind: "system", speaker: { type: "system" }, streaming: false },
    ]);
  });

  it("snapshot replacement discards queued legacy playback instead of resurrecting future text", () => {
    onNarrativeChunk("不应在读档后出现的未来叙事");
    expect(hasActiveNarrativeStream()).toBe(true);
    message("session_snapshot", {
      message_history: {
        messages: [
          {
            message_id: "saved",
            sequence: 1,
            text: "存档前的叙事。",
            speaker: { kind: "keeper", name: "守秘人" },
          },
        ],
        next_before_sequence: null,
      },
    });
    expect(hasActiveNarrativeStream()).toBe(false);
    vi.runAllTimers();
    expect(useMessageStore.getState().messages).toMatchObject([
      { text: "存档前的叙事。", streaming: false },
    ]);
    expect(useMessageStore.getState().messages).toHaveLength(1);
  });

  it("an authoritative playing snapshot clears a stale starting flag even if gameStarted was true", () => {
    useStartStore.setState({ gameStarted: true, gameStarting: true });
    message("session_snapshot", {});
    expect(useStartStore.getState()).toMatchObject({
      gameStarted: true,
      gameStarting: false,
    });
  });
});
