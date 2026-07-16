import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  addMsg,
  beginNarrativeReplacement,
  branchSourceTurnId,
  completeNarrativeReplacement,
  flushNarrativeStream,
  onNarrativeChunk,
  renderTurnHistory,
  setDisplayTurnId,
} from "./renderer";
import { useMessageStore } from "./state/message-store";

describe("React message renderer adapter", () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: [],
      scrollRequest: 0,
      forceScrollRequest: 0,
    });
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

  it("branches from before the action represented by a result turn", () => {
    expect(
      branchSourceTurnId({ turn_id: "result", parent_turn_id: "decision" }),
    ).toBe("decision");
    expect(
      branchSourceTurnId({ turn_id: "opening", parent_turn_id: null }),
    ).toBe("opening");
  });
});
