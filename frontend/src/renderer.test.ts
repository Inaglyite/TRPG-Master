import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  accelerateNarrativeChoices,
  addMsg,
  beginNarrativeReplacement,
  branchSourceTurnId,
  completeNarrativeReplacement,
  finishNarrativeStream,
  flushNarrativeStream,
  onNarrativeChunk,
  onNarrativeSegment,
  onNarrativeSegments,
  renderTurnHistory,
  revealNarrativeImmediately,
  setDisplayTurnId,
  whenNarrativePresented,
} from "./renderer";
import { useMessageStore } from "./state/message-store";

describe("React message renderer adapter", () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: [],
      scrollRequest: 0,
      forceScrollRequest: 0,
    });
    // 清理 renderer 模块级的流状态（streamMessageId/段结构），保证测试隔离
    revealNarrativeImmediately();
    finishNarrativeStream();
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
  });

  it("stores messages with authoritative turn identity", () => {
    setDisplayTurnId("turn-1");
    addMsg("player", "检查门锁");
    expect(useMessageStore.getState().messages[0]).toMatchObject({
      kind: "player",
      text: "检查门锁",
      turnId: "turn-1",
    });
  });

  it("batches narrative chunks into one streaming message", () => {
    setDisplayTurnId("turn-2");
    onNarrativeChunk("雨落");
    onNarrativeChunk("在窗外。");
    flushNarrativeStream();
    expect(useMessageStore.getState().messages).toHaveLength(1);
    expect(useMessageStore.getState().messages[0]).toMatchObject({
      kind: "gm",
      text: "雨落在窗外。",
      streaming: true,
      turnId: "turn-2",
    });
  });

  it("plays provider text at a paced presentation speed", () => {
    vi.useFakeTimers();
    try {
      setDisplayTurnId("turn-paced");
      onNarrativeChunk("雨。风");

      expect(useMessageStore.getState().messages[0].text).toBe("");
      vi.advanceTimersByTime(34);
      expect(useMessageStore.getState().messages[0].text).toBe("雨");
      vi.advanceTimersByTime(34);
      expect(useMessageStore.getState().messages[0].text).toBe("雨。");
      vi.advanceTimersByTime(120);
      expect(useMessageStore.getState().messages[0].text).toBe("雨。");
      vi.advanceTimersByTime(65);
      expect(useMessageStore.getState().messages[0].text).toBe("雨。风");
    } finally {
      revealNarrativeImmediately();
      finishNarrativeStream();
      vi.useRealTimers();
    }
  });

  it("keeps the turn presenting after network done until the queue drains", () => {
    vi.useFakeTimers();
    try {
      let presented = false;
      setDisplayTurnId("turn-done-order");
      onNarrativeChunk("缓慢出现");
      finishNarrativeStream();
      whenNarrativePresented(() => {
        presented = true;
      });

      expect(presented).toBe(false);
      vi.runAllTimers();
      expect(presented).toBe(true);
      expect(useMessageStore.getState().messages[0]).toMatchObject({
        text: "缓慢出现",
        streaming: false,
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("accelerates when a large provider backlog accumulates", () => {
    vi.useFakeTimers();
    try {
      setDisplayTurnId("turn-catch-up");
      onNarrativeChunk("字".repeat(600));
      vi.advanceTimersByTime(34);

      expect(useMessageStore.getState().messages[0].text).toBe("字字字");
    } finally {
      revealNarrativeImmediately();
      finishNarrativeStream();
      vi.useRealTimers();
    }
  });

  it("switches the final action menu to fast playback", () => {
    vi.useFakeTimers();
    try {
      setDisplayTurnId("turn-fast-choices");
      onNarrativeChunk("正文你可以——1. 去办公室");

      vi.advanceTimersByTime(68);
      expect(useMessageStore.getState().messages[0].text).toBe("正文");
      vi.advanceTimersByTime(34);
      expect(
        useMessageStore.getState().messages[0].text.length,
      ).toBeGreaterThan(3);

      onNarrativeChunk("2. 去小屋");
      accelerateNarrativeChoices(true);
      vi.runAllTimers();
      expect(useMessageStore.getState().messages[0].text).toContain("去小屋");
    } finally {
      revealNarrativeImmediately();
      finishNarrativeStream();
      vi.useRealTimers();
    }
  });

  it("atomically replaces a rewritten narrative", () => {
    renderTurnHistory([
      { turn_id: "source", player_input: "开门", narrative: "旧叙述" },
    ]);
    setDisplayTurnId("rewrite-turn");
    beginNarrativeReplacement("source");
    onNarrativeChunk("新叙述");
    flushNarrativeStream();
    completeNarrativeReplacement("source");

    const messages = useMessageStore.getState().messages;
    expect(messages.filter((message) => message.kind === "gm")).toHaveLength(1);
    expect(messages.find((message) => message.kind === "gm")).toMatchObject({
      text: "新叙述",
      turnId: "source",
      streaming: false,
    });
  });

  it("migrates legacy narrative-only history into a keeper chat event", () => {
    renderTurnHistory([
      { turn_id: "legacy", player_input: "开门", narrative: "旧叙述" },
    ]);

    const gm = useMessageStore
      .getState()
      .messages.find((message) => message.kind === "gm");
    expect(gm?.segments).toEqual([
      expect.objectContaining({
        eventId: "turn-legacy-legacy",
        kind: "narration",
        text: "旧叙述",
      }),
    ]);
  });

  it("branches from before the action represented by a result turn", () => {
    expect(
      branchSourceTurnId({ turn_id: "result", parent_turn_id: "decision" }),
    ).toBe("decision");
    expect(
      branchSourceTurnId({ turn_id: "opening", parent_turn_id: null }),
    ).toBe("opening");
  });

  it("builds live speech segments from npc-tagged chunks", () => {
    setDisplayTurnId("turn-9");
    onNarrativeSegment({
      type: "npc",
      id: "bryce_fallon",
      name: "布莱斯·法伦",
    });
    onNarrativeChunk("雨还在下。");
    onNarrativeChunk("「莱特生前一直在隐瞒什么。」", "bryce_fallon");
    onNarrativeChunk("他说完看向抽屉。");
    flushNarrativeStream();

    const gm = useMessageStore
      .getState()
      .messages.find((message) => message.kind === "gm");
    expect(gm?.segments?.map((segment) => segment.kind)).toEqual([
      "narration",
      "speech",
      "narration",
    ]);
    expect(gm?.segments?.[1].speaker?.name).toBe("布莱斯·法伦");
    expect(gm?.segments?.[1].text).toContain("隐瞒");
    expect(gm?.text).toContain("他说完看向抽屉。");
  });

  it("backfills a live speech placeholder when speaker identity arrives", () => {
    setDisplayTurnId("turn-late-speaker");
    onNarrativeChunk("「门打开以后，空气里全是墨水味。」", "bryce_late");
    flushNarrativeStream();

    let gm = useMessageStore
      .getState()
      .messages.find((message) => message.kind === "gm");
    expect(gm?.segments?.[0]).toMatchObject({
      kind: "speech",
      npcId: "bryce_late",
    });
    expect(gm?.segments?.[0].speaker).toBeUndefined();

    onNarrativeSegment({
      type: "npc",
      id: "bryce_late",
      name: "布莱斯·法伦",
      avatar: { asset_url: "/api/assets/scarlet/bryce.png" },
    });

    gm = useMessageStore
      .getState()
      .messages.find((message) => message.kind === "gm");
    expect(gm?.segments?.[0].speaker).toMatchObject({
      id: "bryce_late",
      name: "布莱斯·法伦",
      avatar: { asset_url: "/api/assets/scarlet/bryce.png" },
    });
  });

  it("renders speaker identity before the first npc text chunk", () => {
    setDisplayTurnId("turn-inline-speaker");
    onNarrativeSegment({
      type: "npc",
      id: "bryce_inline",
      name: "布莱斯·法伦",
      avatar: { asset_url: "/api/assets/scarlet/bryce.png" },
    });
    onNarrativeChunk("「黄先生，请坐。」", "bryce_inline");
    flushNarrativeStream();

    const speech = useMessageStore
      .getState()
      .messages.find((message) => message.kind === "gm")?.segments?.[0];
    expect(speech).toMatchObject({
      kind: "speech",
      npcId: "bryce_inline",
      speaker: {
        id: "bryce_inline",
        name: "布莱斯·法伦",
        avatar: { asset_url: "/api/assets/scarlet/bryce.png" },
      },
    });
  });

  it("applies authoritative segments over the live stream", () => {
    setDisplayTurnId("turn-10");
    onNarrativeChunk("全部文本。");
    onNarrativeSegments([
      { kind: "narration", text: "第一段。" },
      {
        kind: "speech",
        text: "「第二句。」",
        speaker: { type: "npc", id: "x", name: "某人" },
      },
    ]);
    flushNarrativeStream();

    const gm = useMessageStore
      .getState()
      .messages.find((message) => message.kind === "gm");
    expect(gm?.segments).toHaveLength(2);
    expect(gm?.segments?.[1].speaker?.name).toBe("某人");
  });

  it("uses late authoritative speakers while paced text is still playing", () => {
    vi.useFakeTimers();
    try {
      setDisplayTurnId("turn-rebased-speakers");
      onNarrativeChunk("雨声。“请坐。”门关上了。");
      onNarrativeSegments([
        { kind: "narration", text: "雨声。" },
        {
          kind: "speech",
          text: "“请坐。”",
          npc_id: "fallon",
          speaker: { type: "npc", id: "fallon", name: "法伦" },
        },
        { kind: "narration", text: "门关上了。" },
      ]);

      vi.runAllTimers();
      const gm = useMessageStore.getState().messages[0];
      expect(gm.segments?.map((segment) => segment.kind)).toEqual([
        "narration",
        "speech",
        "narration",
      ]);
      expect(gm.segments?.[1].speaker?.name).toBe("法伦");
      expect(gm.text).toBe("雨声。“请坐。”门关上了。");
    } finally {
      revealNarrativeImmediately();
      finishNarrativeStream();
      vi.useRealTimers();
    }
  });

  it("carries segments through turn history replay", () => {
    renderTurnHistory([
      {
        turn_id: "t-hist",
        player_input: "开门",
        narrative: "旧叙述",
        narrative_segments: [
          { kind: "narration", text: "旧叙述" },
          {
            kind: "speech",
            text: "「旧台词」",
            speaker: { type: "npc", id: "y", name: "旧人" },
          },
        ],
      },
    ]);

    const gm = useMessageStore
      .getState()
      .messages.find((message) => message.kind === "gm");
    expect(gm?.segments?.[1].speaker?.name).toBe("旧人");
  });

  it("prefers authoritative chat events and normalizes wire field names", () => {
    renderTurnHistory([
      {
        turn_id: "t-chat",
        narrative: "法伦说话。",
        narrative_segments: [{ kind: "narration", text: "旧兼容段" }],
        chat_events: [
          {
            kind: "speech",
            text: "黄先生，我需要你查明真相。",
            event_id: "event-1",
            npc_id: "bryce_fallon",
            speaker: { type: "npc", id: "bryce_fallon", name: "法伦" },
          },
        ],
      },
    ]);

    const segment = useMessageStore.getState().messages[0].segments?.[0];
    expect(segment).toMatchObject({
      eventId: "event-1",
      npcId: "bryce_fallon",
      speaker: { name: "法伦" },
    });
    expect(segment?.text).not.toContain("旧兼容段");
  });
});
