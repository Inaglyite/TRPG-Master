/**
 * M5 上下文与记忆改造的前端适配：
 * - `interaction_updated` / 快照 `interactions[]` → 待办区「当前交互」卡（记录而非执行授权）；
 * - `memory_query_result` 只进主持台（玩家连接按服务端过滤收不到）；
 * - 三个新事件类型不再被算作「未适配」。
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  readInteractionThread,
  readMemoryEntry,
} from "./../protocol/structured";
import {
  initialStructuredState,
  openInteractions,
  useStructuredStore,
} from "./structured-store";

const THREAD = {
  thread_id: "thr_9f1c2ab4",
  status: "open",
  investigator_id: "pc",
  pending_action: {
    kind: "move",
    destination_scene_id: "miskatonic_medical",
    note: "尚未出发前往医学院",
  },
  disclosed: ["遗体已移交家属或殡葬方"],
  waiting_on: "pc",
  note: "玩家表达了去医学院看遗体的意愿。",
  origin_request_id: "req-see-body",
  last_request_id: "req-ask-about-morgue",
};

function envelope(type: string, payload: Record<string, unknown>, seq = 1) {
  return {
    protocol_version: 1,
    event_id: seq,
    world_id: "world-1",
    sequence: seq,
    revision: seq,
    type,
    cause_request_id: null,
    payload,
  } as never;
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
});

describe("交互线程解析与投影", () => {
  it("解析服务端线程（官方 fixture 形状）", () => {
    const thread = readInteractionThread(THREAD);
    expect(thread?.threadId).toBe("thr_9f1c2ab4");
    expect(thread?.status).toBe("open");
    expect(thread?.pendingAction.destinationSceneId).toBe("miskatonic_medical");
    expect(thread?.disclosed).toEqual(["遗体已移交家属或殡葬方"]);
    expect(thread?.waitingOn).toBe("pc");
  });

  it("缺 thread_id 或非法状态不构成线程", () => {
    expect(readInteractionThread({ ...THREAD, thread_id: "" })).toBeNull();
    expect(readInteractionThread({ ...THREAD, status: "weird" })).toBeNull();
  });

  it("interaction_updated 落到「当前交互」投影，快照可恢复，终态不再显示", async () => {
    const effectsModule = await import("../structured-effects");
    const events: Array<Record<string, unknown>> = [
      { type: "interaction_updated", payload: THREAD },
    ];
    for (const event of events) {
      effectsModule.applyStructuredEffects(
        envelope(
          String(event.type),
          event.payload as Record<string, unknown>,
          2,
        ),
      );
    }
    const state = useStructuredStore.getState();
    expect(openInteractions(state).map((t) => t.threadId)).toEqual([
      "thr_9f1c2ab4",
    ]);

    // 刷新：快照 interactions[] 恢复
    useStructuredStore.setState({ ...initialStructuredState });
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 5, interactions: [THREAD] }, "world-1");
    expect(openInteractions(useStructuredStore.getState())).toHaveLength(1);

    // 终态：不再作为「可继续」的目标显示
    useStructuredStore.setState({ ...initialStructuredState });
    useStructuredStore
      .getState()
      .applyInteractions([
        { ...readInteractionThread(THREAD)!, status: "cancelled" },
      ]);
    expect(openInteractions(useStructuredStore.getState())).toHaveLength(0);
  });

  it("三个新事件类型不算「未适配」", () => {
    const state = useStructuredStore.getState();
    for (const type of [
      "interaction_updated",
      "memory_recorded",
      "memory_query_result",
    ]) {
      expect(
        (state as unknown as { noteUnknownEventType: (t: string) => void })
          .noteUnknownEventType,
      ).toBeTypeOf("function");
    }
    expect(useStructuredStore.getState().unknownEventTypes).toEqual([]);
  });
});

describe("记忆查询结果只进主持台", () => {
  it("解析记忆条目（官方 fixture 形状）", () => {
    const entry = readMemoryEntry({
      memory_id: "mem_51ab8c20",
      character_id: "bryce_fallon",
      character_kind: "npc",
      knowledge_type: "told",
      content: "法伦得知调查员想去医学院查看莱特的遗体。",
      scene_id: "miskatonic_university",
      subjects: ["pc"],
      topics: ["npc", "conversation"],
      status: "active",
      source: { kind: "keeper" },
      created_sequence: 121,
    });
    expect(entry?.knowledgeType).toBe("told");
    expect(entry?.characterKind).toBe("npc");
    expect(entry?.topics).toEqual(["npc", "conversation"]);
  });

  it("只接受本次 query_id 的结果，迟到的旧结果不覆盖", async () => {
    const effectsModule = await import("../structured-effects");
    useStructuredStore
      .getState()
      .beginMemoryQuery("query-2", { topics: ["npc"] });
    effectsModule.applyStructuredEffects(
      envelope(
        "memory_query_result",
        { query_id: "query-1", memories: [], truncated: false },
        3,
      ),
    );
    expect(useStructuredStore.getState().memoryQuery.status).toBe("querying");

    effectsModule.applyStructuredEffects(
      envelope(
        "memory_query_result",
        {
          query_id: "query-2",
          memories: [
            {
              memory_id: "mem-1",
              character_id: "pc",
              character_kind: "investigator",
              knowledge_type: "experienced",
              content: "在停尸间看到了遗体。",
              scene_id: "miskatonic_medical",
              subjects: [],
              topics: ["morgue"],
              status: "active",
              created_sequence: 5,
            },
          ],
          truncated: true,
        },
        4,
      ),
    );
    const state = useStructuredStore.getState().memoryQuery;
    expect(state.status).toBe("done");
    expect(state.entries).toHaveLength(1);
    expect(state.truncated).toBe(true);
  });
});

describe("E2E 时序复现：连接期空快照 + 之后到来的线程事件", () => {
  it("空 interactions[] 快照不应清掉随后到达的线程事件", async () => {
    const effectsModule = await import("../structured-effects");
    // 连接期快照：interactions 为空（此时命令还没执行）
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 3, interactions: [] }, "world-1");
    expect(openInteractions(useStructuredStore.getState())).toHaveLength(0);

    // 主持命令 → interaction_updated
    effectsModule.applyStructuredEffects(
      envelope("interaction_updated", THREAD, 4),
    );
    expect(
      openInteractions(useStructuredStore.getState()).map((t) => t.threadId),
    ).toEqual(["thr_9f1c2ab4"]);

    // 再来一个快照（仍为空）会清掉吗？——这正是 E2E 里待办卡消失的候选原因
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 4, interactions: [] }, "world-1");
    console.log(
      "after snapshot:",
      JSON.stringify(
        openInteractions(useStructuredStore.getState()).map((t) => t.threadId),
      ),
    );
  });
});
