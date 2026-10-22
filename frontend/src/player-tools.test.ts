import { describe, expect, it } from "vitest";
import { currentTurnSource, digestNote, mapLayout } from "./player-tools";

describe("player tool sources", () => {
  it("uses the latest completed displayed turn and excludes user, dice, hidden and streaming text", () => {
    const source = currentTurnSource([
      { id: "old", turnId: "t1", kind: "gm", text: "旧一轮" },
      { id: "u", kind: "player", text: "玩家猜测" },
      {
        id: "a",
        turnId: "t2",
        kind: "gm",
        text: "keeper",
        segments: [{ kind: "narration", text: "门已锁好。" }],
      },
      { id: "dice", kind: "dice", text: "随机骰点" },
      {
        id: "b",
        turnId: "t2",
        kind: "gm",
        text: "spoken",
        segments: [
          {
            kind: "speech",
            text: "昨夜听见钟声。",
            speaker: { type: "npc", name: "门房" },
          },
        ],
      },
      { id: "hidden", kind: "gm", hidden: true, text: "主持秘密" },
      { id: "stream", kind: "gm", streaming: true, text: "未定稿的猜测" },
    ]);
    expect(source).toEqual({
      id: "t2",
      label: "第 2 轮叙述",
      text: "门已锁好。\n\n门房：昨夜听见钟声。",
    });
  });

  it("supports structured narration grouped after the latest player declaration", () => {
    expect(
      currentTurnSource([
        { id: "old", kind: "gm", text: "旧场景" },
        {
          id: "intent",
          kind: "player",
          entryKind: "action_request",
          text: "询问门房",
        },
        { id: "new", kind: "gm", text: "门房停下扫帚。" },
      ])?.text,
    ).toBe("门房停下扫帚。");
    expect(
      currentTurnSource([{ id: "err", kind: "error", text: "调用失败" }]),
    ).toBeNull();
  });

  it("a failed later turn cannot replace the latest committed source", () => {
    const source = currentTurnSource(
      [
        { id: "done", turnId: "t1", kind: "gm", text: "已提交的观察。" },
        { id: "player", kind: "player", text: "继续搜查" },
        {
          id: "failed",
          turnId: "t2",
          kind: "gm",
          text: "尚未提交的半截结果。",
        },
        { id: "error", kind: "error", text: "本轮失败" },
      ],
      "t1",
    );
    expect(source?.text).toBe("已提交的观察。");
  });

  it("keeps evidence in the player's note and computes no hidden graph placeholders", () => {
    expect(
      digestNote({ label: "第 2 轮叙述" }, [
        { text: "钟声有待核实。", quote: "昨夜\n听见钟声" },
      ]),
    ).toContain("原文：昨夜 听见钟声");
    const layout = mapLayout({
      nodes: [
        { id: "b", name: "街口", current: false },
        { id: "a", name: "书房", current: true },
      ],
      edges: [{ from: "a", to: "b" }],
    });
    expect(layout.nodes.map((node) => node.id)).toEqual(["a", "b"]);
    expect(layout.nodes).toHaveLength(2);
    expect(layout.width).toBe(440);
  });
});
