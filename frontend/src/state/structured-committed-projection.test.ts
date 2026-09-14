/**
 * 结构化模式的「位置与状态只由服务端已提交投影驱动」：
 * - 叙事文本里写到「到了/走进/前往」不改变场景指示器；
 * - 玩家文本里的「去/继续/取消」不改变待办或请求状态（前端没有关键词解释器）；
 * - 只有 `scene_changed` / `session_snapshot` 与 `action_status` 才改变这些状态。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { applyStructuredEffects } from "../structured-effects";
import { useSceneStore } from "./scene-store";
import { initialStructuredState, useStructuredStore } from "./structured-store";
import { useMessageStore } from "./message-store";

vi.mock("../renderer", () => ({
  onDice: vi.fn(),
  onNarrativeChunk: vi.fn(),
  onNarrativeSegment: vi.fn(),
}));
vi.mock("../panels", () => ({
  showHandout: vi.fn(),
  updateCharPanel: vi.fn(),
}));

const WORLD = "world-1";

function envelope(type: string, payload: Record<string, unknown>, seq = 1) {
  return {
    protocol_version: 1,
    event_id: seq,
    world_id: WORLD,
    sequence: seq,
    revision: seq,
    type,
    cause_request_id: null,
    payload,
  } as never;
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useSceneStore.getState().reset();
  useMessageStore.setState({ messages: [] });
});

describe("位置只由已提交投影更新", () => {
  it("叙事文本里说『到了医学院』不会改变场景指示器", () => {
    useSceneStore.getState().applyScene(WORLD, {
      id: "miskatonic_university",
      name: "密斯卡托尼克大学",
    });
    applyStructuredEffects(
      envelope("message_completed", {
        message_id: "msg-1",
        speaker: { kind: "keeper" },
        text: "你们沿着小径走了十分钟，医学院的灰石楼就在眼前——你们到了。",
      }),
    );
    expect(useSceneStore.getState().name).toBe("密斯卡托尼克大学");
  });

  it("scene_changed 才更新位置", () => {
    applyStructuredEffects(
      envelope("scene_changed", {
        scene: { id: "miskatonic_medical", name: "密斯卡托尼克大学医学院" },
      }),
    );
    expect(useSceneStore.getState().name).toBe("密斯卡托尼克大学医学院");
  });
});

describe("前端没有玩家文本关键词解释器", () => {
  it("含『取消/继续/去』的消息事件不动待办状态", () => {
    useStructuredStore.getState().registerOutgoing({
      requestId: "req-1",
      kind: "freeform",
      label: "行动",
      payload: { text: "我想先看看尸体。" },
      digest: "d1",
    });
    useStructuredStore.getState().applyEvent(
      envelope(
        "action_status",
        {
          request_id: "req-1",
          status: "awaiting_player",
          awaiting: {
            pending_action: {
              kind: "move",
              destination_scene_id: "morgue",
              note: "尚未出发",
            },
            disclosed: ["需要医生放行"],
          },
        },
        2,
      ),
    );
    expect(useStructuredStore.getState().requests["req-1"].status).toBe(
      "awaiting_player",
    );

    // 玩家（或别人的）聊天里出现「取消/继续/过去」等词，只是一条消息：
    for (const [seq, text] of [
      [3, "算了，取消吧。"],
      [4, "继续。"],
      [5, "我现在过去。"],
    ] as const) {
      applyStructuredEffects(
        envelope(
          "message_completed",
          { message_id: `msg-${seq}`, speaker: { kind: "investigator" }, text },
          seq,
        ),
      );
    }
    const request = useStructuredStore.getState().requests["req-1"];
    expect(request.status).toBe("awaiting_player");
    expect(request.awaiting?.note).toBe("尚未出发");
  });
});

describe("未适配的结构化事件不会被静默丢弃", () => {
  it("M5 已适配的事件类型不再算未适配", () => {
    applyStructuredEffects(
      envelope("interaction_updated", {
        thread_id: "th-1",
        status: "open",
        investigator_id: "pc",
        pending_action: { kind: "move", note: "尚未出发" },
        disclosed: ["需要医生放行"],
        waiting_on: "pc",
        origin_request_id: "req-1",
        last_request_id: "req-2",
      }),
    );
    expect(useStructuredStore.getState().unknownEventTypes).toEqual([]);
  });

  it("真正未知的类型仍被记录并可见", () => {
    applyStructuredEffects(envelope("some_future_event", { anything: true }));
    expect(useStructuredStore.getState().unknownEventTypes).toEqual([
      "some_future_event",
    ]);
  });

  it("已登记的事件类型不会被记成未适配", () => {
    applyStructuredEffects(
      envelope("message_completed", {
        message_id: "msg-9",
        speaker: { kind: "keeper" },
        text: "例子",
      }),
    );
    expect(useStructuredStore.getState().unknownEventTypes).toEqual([]);
  });
});
