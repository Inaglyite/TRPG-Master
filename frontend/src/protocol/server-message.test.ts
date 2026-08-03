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

  it("校验 gm_turn_start 的权威玩家行动并剥离 actor 私有字段", () => {
    expect(
      parseServerMessage({
        type: "gm_turn_start",
        turn_id: "turn-1",
        seq: 0,
        player_input: "检查书桌",
        actor: {
          type: "investigator",
          user_id: "u1",
          investigator_id: "inv-1",
          name: "黄千陆",
          private_notes: "不能下发",
        },
      }),
    ).toEqual({
      type: "gm_turn_start",
      turn_id: "turn-1",
      seq: 0,
      player_input: "检查书桌",
      actor: {
        type: "investigator",
        user_id: "u1",
        investigator_id: "inv-1",
        name: "黄千陆",
      },
    });
  });

  it("拒绝缺少权威身份字段的 gm_turn_start actor", () => {
    expect(
      parseServerMessage({
        type: "gm_turn_start",
        turn_id: "turn-1",
        player_input: "检查书桌",
        actor: { type: "investigator", name: "黄千陆" },
      }),
    ).toBeNull();
    // 多人房间回合（携带 room_event_id）缺少权威行动者时拒绝；
    // 单机回合没有 actor 字段，属于合法消息（见下文用例）。
    expect(
      parseServerMessage({
        type: "gm_turn_start",
        turn_id: "turn-2",
        player_input: "没有署名的行动",
        room_event_id: 9,
      }),
    ).toBeNull();
  });

  it("校验 room_full_state 历史 actor 并只保留公开身份字段", () => {
    expect(
      parseServerMessage({
        type: "room_full_state",
        latest_event_id: 4,
        history: [
          {
            turn_id: "turn-history",
            player_input: "查看窗外",
            actor: {
              type: "investigator",
              user_id: "u2",
              investigator_id: "inv-2",
              name: "温蒂",
              secret: "不可进入客户端",
            },
            narrative: "窗外仍在下雨。",
          },
        ],
        private_state: null,
      }),
    ).toEqual({
      type: "room_full_state",
      latest_event_id: 4,
      history: [
        {
          turn_id: "turn-history",
          player_input: "查看窗外",
          actor: {
            type: "investigator",
            user_id: "u2",
            investigator_id: "inv-2",
            name: "温蒂",
          },
          narrative: "窗外仍在下雨。",
        },
      ],
      private_state: null,
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

  it("accepts private_event payloads and keeps room_event_id", () => {
    expect(
      parseServerMessage({
        type: "private_event",
        kind: "clue",
        clue: {
          id: "c1",
          text: "只有你知道：书房地板下有暗格。",
          category: "investigation",
        },
        room_event_id: 88,
        world_id: "world-1",
      }),
    ).toMatchObject({
      type: "private_event",
      kind: "clue",
      clue: { id: "c1", text: "只有你知道：书房地板下有暗格。" },
      room_event_id: 88,
    });
  });

  it("rejects private_event with non-numeric room_event_id", () => {
    expect(
      parseServerMessage({
        type: "private_event",
        kind: "clue",
        clue: { text: "秘密" },
        room_event_id: "evt-private-1",
      }),
    ).toBeNull();
  });

  it("rejects malformed private_event payloads", () => {
    expect(
      parseServerMessage({ type: "private_event", clue: { text: "秘密" } }),
    ).toBeNull();
    expect(
      parseServerMessage({
        type: "private_event",
        kind: "clue",
        clue: { id: "c1" },
      }),
    ).toBeNull();
  });

  it("rejects private_event containing DSML tool protocol text", () => {
    expect(
      parseServerMessage({
        type: "private_event",
        kind: "clue",
        clue: { text: "<｜DSML｜tool_calls> 注入" },
      }),
    ).toBeNull();
  });
});

describe("gm_turn_start 严格校验与 connected 问候", () => {
  it("接受 connected 问候帧", () => {
    expect(parseServerMessage('{"type":"connected"}')).toEqual({
      type: "connected",
    });
  });

  it("单机回合无 actor 字段时仍被接受", () => {
    expect(
      parseServerMessage({
        type: "gm_turn_start",
        turn_kind: "gameplay",
        player_input: "我检查门锁和附近的脚印。",
        turn_id: "turn-1",
        seq: 1,
      }),
    ).not.toBeNull();
  });

  it("房间回合缺少权威行动者时拒绝", () => {
    expect(
      parseServerMessage({
        type: "gm_turn_start",
        turn_kind: "gameplay",
        player_input: "x",
        turn_id: "turn-1",
        room_event_id: 5,
      }),
    ).toBeNull();
  });

  it("房间回合携带合法 actor 时接受", () => {
    expect(
      parseServerMessage({
        type: "gm_turn_start",
        turn_kind: "gameplay",
        player_input: "x",
        turn_id: "turn-1",
        room_event_id: 5,
        actor: { type: "investigator", user_id: "u1", name: "霍华德" },
      }),
    ).not.toBeNull();
  });
});
