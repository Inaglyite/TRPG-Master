/**
 * 读档后的陈旧内容治理（结构化世界）：
 * 结构化世界没有消息历史，读档是 CAS 回滚 —— 存档点之后的聊天不能继续显示，
 * 否则玩家会看到「未来事件」与旧世界对白。legacy 路径保持原行为（服务端会回放消息）。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { handleServerPayload } from "./ws";
import { useMessageStore } from "./state/message-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "./state/structured-store";
import { readServerCapabilities } from "./protocol/structured";
import { STRUCTURED_CAPABILITIES_WIRE } from "./protocol/structured-fixtures";

vi.mock("./renderer", async () => {
  const actual =
    await vi.importActual<typeof import("./renderer")>("./renderer");
  return {
    ...actual,
    onDice: vi.fn(),
    onNarrativeChunk: vi.fn(),
    onNarrativeSegment: vi.fn(),
  };
});

function seedChat() {
  useMessageStore.setState({
    messages: [
      { id: "m1", role: "system", text: "开局", ts: 1 } as never,
      { id: "m2", role: "keeper", text: "你们走进医学院。", ts: 2 } as never,
    ],
  });
}

beforeEach(() => {
  seedChat();
  useStructuredStore.setState({ ...initialStructuredState });
});

describe("读档后的陈旧内容", () => {
  it("结构化世界读档成功：清空聊天区，不显示存档点之后的对话", () => {
    useStructuredStore
      .getState()
      .applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
    handleServerPayload({
      type: "loaded",
      ok: true,
      count: 0,
      slot_id: "slot_001",
    });
    const messages = useMessageStore.getState().messages;
    expect(messages).toHaveLength(1); // 只剩一条系统提示
    expect(String(messages[0]?.text ?? "")).toContain("已回到存档点");
    expect(
      messages.some((m) => String(m.text).includes("你们走进医学院")),
    ).toBe(false);
  });

  it("legacy 世界读档保持原行为（提示恢复条数，不清空）", () => {
    useStructuredStore
      .getState()
      .applyCapabilities(
        readServerCapabilities({ execution_profile: "legacy" }),
      );
    handleServerPayload({
      type: "loaded",
      ok: true,
      count: 2,
      slot_id: "slot_001",
    });
    const messages = useMessageStore.getState().messages;
    expect(messages).toHaveLength(3);
    expect(String(messages.at(-1)?.text ?? "")).toContain("恢复了 2 条消息");
  });

  it("读档失败不清空聊天区", () => {
    useStructuredStore
      .getState()
      .applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
    handleServerPayload({ type: "loaded", ok: false });
    expect(useMessageStore.getState().messages).toHaveLength(3);
    expect(
      String(useMessageStore.getState().messages.at(-1)?.text ?? ""),
    ).toContain("未找到存档");
  });
});
