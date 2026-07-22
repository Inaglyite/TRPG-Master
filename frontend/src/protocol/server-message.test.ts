import { describe, expect, it } from "vitest";

import { parseServerMessage } from "./server-message";

describe("parseServerMessage", () => {
  it("retains payload fields for a known protocol message", () => {
    expect(
      parseServerMessage('{"type":"narrative_chunk","text":"雨声"}'),
    ).toEqual({
      type: "narrative_chunk",
      text: "雨声",
    });
  });

  it("rejects malformed and unknown messages without throwing", () => {
    expect(parseServerMessage("{")).toBeNull();
    expect(parseServerMessage({ type: "future_message" })).toBeNull();
    expect(parseServerMessage(null)).toBeNull();
  });

  it("accepts turn performance diagnostics", () => {
    expect(
      parseServerMessage({
        type: "turn_performance",
        metrics: { first_visible_ms: 120 },
      }),
    ).toEqual({
      type: "turn_performance",
      metrics: { first_visible_ms: 120 },
    });
  });

  it("validates and strips private fields from authoritative chat events", () => {
    expect(
      parseServerMessage({
        type: "chat_events",
        events: [
          {
            event_id: "event-1",
            kind: "speech",
            text: "黄先生，请坐。",
            npc_id: "bryce_fallon",
            private_memory: "绝不能传给玩家",
            speaker: {
              type: "npc",
              id: "bryce_fallon",
              name: "法伦",
              secret: "隐藏动机",
            },
          },
        ],
      }),
    ).toEqual({
      type: "chat_events",
      events: [
        {
          event_id: "event-1",
          kind: "speech",
          text: "黄先生，请坐。",
          npc_id: "bryce_fallon",
          speaker: { type: "npc", id: "bryce_fallon", name: "法伦" },
        },
      ],
    });
  });

  it("rejects malformed chat event payloads", () => {
    expect(
      parseServerMessage({
        type: "chat_events",
        events: [{ kind: "private_thought", text: "秘密" }],
      }),
    ).toBeNull();
  });

  it("rejects textual DSML tool protocols with repeated full-width bars", () => {
    expect(
      parseServerMessage({
        type: "narrative_chunk",
        text: '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="npc_reveal">秘密',
      }),
    ).toBeNull();
  });
});
