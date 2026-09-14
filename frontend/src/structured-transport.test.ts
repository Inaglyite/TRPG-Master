import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACK_TIMEOUT_MS,
  currentStructuredIdentity,
  handleStructuredPayload,
  rebindStructuredWorld,
  resetStructuredTransport,
  resendStructuredRequest,
  sendCheckResponse,
  sendFreeRoll,
  sendKeeperCommand,
  sendStructuredAction,
  setStructuredSender,
  structuredCursor,
} from "./structured-transport";
import { useAppStore } from "./state/app-store";
import { useOnlineStore } from "./state/online-store";
import {
  activeRequests,
  initialStructuredState,
  pendingChecksFor,
  visibleChecks,
  useStructuredStore,
} from "./state/structured-store";
import {
  EVENT_FIXTURES,
  HUMAN_ONLY_CAPABILITIES_WIRE,
  LEGACY_CAPABILITIES,
  STRUCTURED_CAPABILITIES_WIRE,
  WORLD_ID,
} from "./protocol/structured-fixtures";

/** 记录实际发出的帧，用于断言“按钮传的是结构请求”。 */
let sent: Record<string, unknown>[] = [];

function enableStructured() {
  handleStructuredPayload(EVENT_FIXTURES.snapshot);
}

beforeEach(() => {
  resetStructuredTransport();
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    inputEnabled: true,
    activeWorldId: WORLD_ID,
  });
  useOnlineStore.setState({ activeInvestigatorId: null });
  sent = [];
  setStructuredSender((payload) => {
    sent.push(payload as Record<string, unknown>);
    return true;
  });
});

describe("能力门禁", () => {
  it("服务端没有结构化能力时拒绝提交，且一个字节都不发", () => {
    useStructuredStore.getState().applyCapabilities(LEGACY_CAPABILITIES);
    const result = sendStructuredAction({
      kind: "move",
      destination_scene_id: "s1",
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toContain("结构化协议");
    expect(sent).toHaveLength(0);
  });

  it("协议版本不匹配时给出明确提示并停止提交", () => {
    handleStructuredPayload({
      ...EVENT_FIXTURES.snapshot,
      protocol_version: 42,
    });
    expect(useStructuredStore.getState().protocolNotice).toContain("不同版本");
    const result = sendStructuredAction({
      kind: "move",
      destination_scene_id: "s1",
    });
    expect(result.ok).toBe(false);
    expect(sent).toHaveLength(0);
  });

  it("缺少世界版本号时拒绝提交（不发 expected_revision=0）", () => {
    useStructuredStore
      .getState()
      .applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
    useStructuredStore.getState().bindWorld("world-x", 0);
    useStructuredStore.getState().setInvestigator("inv-alice");
    const result = sendStructuredAction({
      kind: "move",
      destination_scene_id: "s1",
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toContain("世界版本号");
    expect(sent).toHaveLength(0);
  });

  it("能力可用但未确定调查员时拒绝提交", () => {
    handleStructuredPayload(EVENT_FIXTURES.snapshot);
    useStructuredStore.getState().setInvestigator("");
    useOnlineStore.setState({ activeInvestigatorId: null });
    const result = sendStructuredAction({
      kind: "move",
      destination_scene_id: "s1",
    });
    expect(result.ok).toBe(false);
    expect(sent).toHaveLength(0);
  });
});

describe("按钮提交结构请求", () => {
  it("出示线索：发出的是 action_request，含线索 ID/方式/目标 ID", () => {
    enableStructured();
    const result = sendStructuredAction({
      kind: "present_clue",
      clue_id: "clue_death_certificate",
      presentation: "image",
      physical_item_id: null,
      target: { kind: "npc", id: "john_whitcroft" },
      question: "这上面是你的签名吗？",
    });

    expect(result.ok).toBe(true);
    expect(sent).toHaveLength(1);
    const frame = sent[0];
    expect(frame.type).toBe("action_request");
    expect(frame.protocol_version).toBe(1);
    expect(frame.world_id).toBe(WORLD_ID);
    expect(frame.expected_revision).toBe(12);
    expect(frame.investigator_id).toBe("inv-alice");
    expect(typeof frame.request_id).toBe("string");
    expect(frame.action).toEqual({
      kind: "present_clue",
      clue_id: "clue_death_certificate",
      presentation: "image",
      physical_item_id: null,
      target: { kind: "npc", id: "john_whitcroft" },
      question: "这上面是你的签名吗？",
    });
    // 没有自然语言行动句进入权威通道。
    expect(JSON.stringify(frame)).not.toContain("我向");
    // 请求被登记为 queued，等待服务端 ack。
    const request =
      useStructuredStore.getState().requests[String(frame.request_id)];
    expect(request.status).toBe("queued");
    expect(request.sends).toBe(1);
  });

  it("使用道具：带 item_id/quantity/operation/target；前端不预扣数量", () => {
    enableStructured();
    const before = useStructuredStore
      .getState()
      .items.find((item) => item.id === "item_bandage");
    expect(before?.quantity).toBe(3);

    sendStructuredAction({
      kind: "use_item",
      item_id: "item_bandage",
      quantity: 2,
      operation: "apply",
      target: { kind: "investigator", id: "inv-bob" },
      approach: "先清创",
    });

    expect(sent[0].action).toEqual({
      kind: "use_item",
      item_id: "item_bandage",
      quantity: 2,
      operation: "apply",
      target: { kind: "investigator", id: "inv-bob" },
      approach: "先清创",
    });
    // 库存仍由服务端事件更新：这里数量不变。
    expect(
      useStructuredStore
        .getState()
        .items.find((item) => item.id === "item_bandage")?.quantity,
    ).toBe(3);
  });

  it("前往：只提交目的地 ID", () => {
    enableStructured();
    sendStructuredAction({
      kind: "move",
      destination_scene_id: "miskatonic_medical",
    });
    expect(sent[0].action).toEqual({
      kind: "move",
      destination_scene_id: "miskatonic_medical",
    });
  });

  it("普通掷骰走 free_roll_request，不消耗模型额度字段", () => {
    enableStructured();
    const result = sendFreeRoll("1d100");
    expect(result.ok).toBe(true);
    expect(sent[0]).toEqual({
      type: "free_roll_request",
      protocol_version: 1,
      request_id: expect.any(String),
      world_id: WORLD_ID,
      investigator_id: "inv-alice",
      spec: "1d100",
    });
    expect(sent[0]).not.toHaveProperty("expected_revision");
  });

  it("非法骰式在本地就拒绝，不发帧", () => {
    enableStructured();
    expect(sendFreeRoll("99d1000").ok).toBe(false);
    expect(sent).toHaveLength(0);
  });

  it("主持命令带 command_id，可重试同一 ID", () => {
    enableStructured();
    sendKeeperCommand("advance_time", { minutes: 30 }, "cmd-fixed");
    sendKeeperCommand("advance_time", { minutes: 30 }, "cmd-fixed");
    expect(sent).toHaveLength(2);
    expect(sent[0].command_id).toBe("cmd-fixed");
    expect(sent[1].command_id).toBe("cmd-fixed");
    expect(sent[0]).toEqual(sent[1]);
  });
});

describe("请求生命周期", () => {
  it("ack → processing → completed（领域结果另列，不等于行动成功）", () => {
    enableStructured();
    sendStructuredAction({
      kind: "move",
      destination_scene_id: "miskatonic_medical",
    });
    const requestId = String(sent[0].request_id);

    handleStructuredPayload({
      ...EVENT_FIXTURES.actionAck,
      payload: { ...EVENT_FIXTURES.actionAck.payload, request_id: requestId },
    });
    expect(useStructuredStore.getState().requests[requestId].status).toBe(
      "queued",
    );

    handleStructuredPayload({
      ...EVENT_FIXTURES.actionProcessing,
      payload: { request_id: requestId, status: "processing" },
    });
    expect(useStructuredStore.getState().requests[requestId].status).toBe(
      "processing",
    );

    handleStructuredPayload({
      ...EVENT_FIXTURES.actionCompleted,
      payload: {
        request_id: requestId,
        status: "completed",
        outcome: "success",
        detail: "惠特克罗夫特医生认出了自己的签名。",
      },
    });
    const request = useStructuredStore.getState().requests[requestId];
    expect(request.status).toBe("completed");
    expect(request.outcome).toBe("success");
    expect(request.detail).toContain("认出了自己的签名");
    expect(activeRequests(useStructuredStore.getState())).toHaveLength(0);
  });

  it("declined 显示拒绝原因；failed/paused 都保留在待处理列表", () => {
    enableStructured();
    sendStructuredAction({ kind: "move", destination_scene_id: "s1" });
    const requestId = String(sent[0].request_id);

    handleStructuredPayload({
      protocol_version: 1,
      event_id: 60,
      world_id: WORLD_ID,
      sequence: 60,
      revision: 12,
      type: "action_status",
      payload: {
        request_id: requestId,
        status: "paused",
        detail: "守秘人离线",
      },
    });
    expect(useStructuredStore.getState().requests[requestId].status).toBe(
      "paused",
    );
    expect(activeRequests(useStructuredStore.getState())).toHaveLength(1);

    handleStructuredPayload({
      protocol_version: 1,
      event_id: 61,
      world_id: WORLD_ID,
      sequence: 61,
      revision: 12,
      type: "action_status",
      payload: {
        request_id: requestId,
        status: "declined",
        outcome: "not_executed",
        detail: "门锁着，进不去。",
      },
    });
    const request = useStructuredStore.getState().requests[requestId];
    expect(request.status).toBe("declined");
    expect(request.outcome).toBe("not_executed");
    expect(request.detail).toBe("门锁着，进不去。");
  });

  it("同 revision 的多条聊天事件不会因 revision 相同被丢弃", () => {
    enableStructured();
    const chat = (eventId: number) => ({
      protocol_version: 1,
      event_id: eventId,
      world_id: WORLD_ID,
      sequence: eventId,
      revision: 12,
      type: "message_completed",
      payload: {
        speaker: { type: "npc", id: "bryce_fallon", name: "法伦" },
        text: `第${eventId}句`,
      },
    });
    expect(handleStructuredPayload(chat(20)).kind).toBe("applied");
    expect(handleStructuredPayload(chat(21)).kind).toBe("applied");
    expect(handleStructuredPayload(chat(21)).kind).toBe("duplicate");
    expect(handleStructuredPayload(chat(22)).kind).toBe("applied");
  });

  it("超时未收到 ack：标记为“正在查询原请求状态”，并同 ID 自动重发一次", async () => {
    vi.useFakeTimers();
    try {
      enableStructured();
      sendStructuredAction({ kind: "move", destination_scene_id: "s1" });
      const requestId = String(sent[0].request_id);
      expect(sent).toHaveLength(1);

      vi.advanceTimersByTime(ACK_TIMEOUT_MS + 10);

      const request = useStructuredStore.getState().requests[requestId];
      expect(request.awaitingAck).toBe(true);
      expect(request.sends).toBe(2);
      // 重发的是同一份载荷与同一个 request_id。
      expect(sent).toHaveLength(2);
      expect(sent[1]).toEqual(sent[0]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("手动重试沿用同一 request_id 与载荷", () => {
    enableStructured();
    sendStructuredAction({ kind: "move", destination_scene_id: "s1" });
    const requestId = String(sent[0].request_id);
    sent = [];
    const result = resendStructuredRequest(requestId);
    expect(result.ok).toBe(true);
    expect(sent).toHaveLength(1);
    expect(sent[0].request_id).toBe(requestId);
  });

  it("revision 冲突给出可刷新重提的提示，并保留请求", () => {
    enableStructured();
    sendStructuredAction({ kind: "move", destination_scene_id: "s1" });
    const requestId = String(sent[0].request_id);
    handleStructuredPayload({
      ...EVENT_FIXTURES.revisionConflict,
      payload: {
        ...EVENT_FIXTURES.revisionConflict.payload,
        request_id: requestId,
      },
    });
    const request = useStructuredStore.getState().requests[requestId];
    expect(request.errorCode).toBe("revision_conflict");
    expect(request.errorMessage).toContain("请刷新候选后重新提交");
    // 可重试：请求仍在待处理列表里。
    expect(activeRequests(useStructuredStore.getState())).toHaveLength(1);
    // 冲突事件带着新的世界版本：store 必须前进，否则重试永远还是旧版本。
    expect(useStructuredStore.getState().identity.revision).toBeGreaterThan(12);
  });

  it("目标失效的错误码同样保留原文提示", () => {
    enableStructured();
    sendStructuredAction({
      kind: "use_item",
      item_id: "item_bandage",
      quantity: 1,
      operation: "apply",
    });
    const requestId = String(sent[0].request_id);
    handleStructuredPayload({
      ...EVENT_FIXTURES.staleTarget,
      payload: { ...EVENT_FIXTURES.staleTarget.payload, request_id: requestId },
    });
    const request = useStructuredStore.getState().requests[requestId];
    expect(request.errorCode).toBe("stale_target");
    expect(request.errorMessage).toContain("目标已失效");
  });

  it("主持命令的错误按 command_id 归位（否则命令失败在界面上是静默的）", () => {
    enableStructured();
    sendKeeperCommand(
      "grant_clue",
      { clue_id: "c1", recipient_investigator_ids: ["inv-alice"], basis: "x" },
      "cmd-fixed-err",
    );
    const requestId = useStructuredStore.getState().requestOrder[0];
    handleStructuredPayload({
      protocol_version: 1,
      event_id: 70,
      world_id: WORLD_ID,
      sequence: 70,
      revision: 12,
      type: "request_error",
      cause_request_id: null,
      payload: {
        code: "object_not_found",
        message: "调查员不存在：legacy-pc",
        retryable: false,
        command_id: "cmd-fixed-err",
      },
    });
    const request = useStructuredStore.getState().requests[requestId];
    expect(request.errorCode).toBe("object_not_found");
    expect(request.errorMessage).toContain("调查员不存在");
    expect(request.status).toBe("declined");
  });

  it("未知错误码不丢信息", () => {
    enableStructured();
    sendStructuredAction({ kind: "move", destination_scene_id: "s1" });
    const requestId = String(sent[0].request_id);
    handleStructuredPayload({
      ...EVENT_FIXTURES.unknownCode,
      payload: { ...EVENT_FIXTURES.unknownCode.payload, request_id: requestId },
    });
    expect(useStructuredStore.getState().requests[requestId].errorMessage).toBe(
      "服务端新增的拒绝原因",
    );
  });
});

describe("待检定", () => {
  it("check_requested 持久化；只有被指定的调查员能响应", () => {
    enableStructured();
    handleStructuredPayload(EVENT_FIXTURES.checkRequested);

    const check = useStructuredStore.getState().checks["chk-1"];
    expect(check.skill).toBe("说服");
    expect(check.attempt).toContain("查看遗体");
    expect(check.knownCost).toBe("可能需要出示证件");
    expect(
      pendingChecksFor(useStructuredStore.getState(), "inv-alice"),
    ).toHaveLength(1);

    // 别人（inv-bob）代点：本地拒绝，且不发帧。
    useStructuredStore.getState().setInvestigator("inv-bob");
    useOnlineStore.setState({ activeInvestigatorId: "inv-bob" });
    const wrongPlayer = sendCheckResponse("chk-1", "roll");
    expect(wrongPlayer.ok).toBe(false);
    expect(sent).toHaveLength(0);
    expect(useStructuredStore.getState().checks["chk-1"].status).toBe(
      "pending",
    );

    // 指定玩家可以掷骰。
    useStructuredStore.getState().setInvestigator("inv-alice");
    useOnlineStore.setState({ activeInvestigatorId: "inv-alice" });
    const mine = sendCheckResponse("chk-1", "roll");
    expect(mine.ok).toBe(true);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toEqual({
      type: "check_response",
      protocol_version: 1,
      request_id: expect.any(String),
      world_id: WORLD_ID,
      check_request_id: "chk-1",
      decision: "roll",
    });
  });

  it("放弃检定提交 decline；已结算的检定不重复响应", () => {
    enableStructured();
    handleStructuredPayload(EVENT_FIXTURES.checkRequested);
    expect(sendCheckResponse("chk-1", "decline").ok).toBe(true);
    expect(sent[0].decision).toBe("decline");

    handleStructuredPayload(EVENT_FIXTURES.checkResolved);
    const resolved = useStructuredStore.getState().checks["chk-1"];
    expect(resolved.status).toBe("resolved");
    expect(resolved.result?.roll).toBe(23);
    expect(resolved.result?.targetValue).toBe(55);
    expect(
      pendingChecksFor(useStructuredStore.getState(), "inv-alice"),
    ).toHaveLength(0);

    sent = [];
    const again = sendCheckResponse("chk-1", "roll");
    expect(again.ok).toBe(false);
    expect(sent).toHaveLength(0);
  });

  it("重复投递 check_requested 不覆盖已结算结果", () => {
    enableStructured();
    handleStructuredPayload(EVENT_FIXTURES.checkRequested);
    handleStructuredPayload(EVENT_FIXTURES.checkResolved);
    handleStructuredPayload({ ...EVENT_FIXTURES.checkRequested, event_id: 40 });
    const check = useStructuredStore.getState().checks["chk-1"];
    expect(check.status).toBe("resolved");
    expect(check.result?.roll).toBe(23);
  });

  it("刷新后的快照恢复待检定；快照里消失的待检定标记过期", () => {
    enableStructured();
    handleStructuredPayload(EVENT_FIXTURES.checkRequested);
    // 模拟刷新：同一世界的新快照仍带这条待检定。
    useStructuredStore.getState().applySnapshot(
      {
        ...EVENT_FIXTURES.snapshot.payload,
        pending_checks: [
          {
            check_request_id: "chk-1",
            investigator_id: "inv-alice",
            skill: "说服",
            difficulty: "regular",
          },
        ],
      },
      WORLD_ID,
    );
    expect(
      pendingChecksFor(useStructuredStore.getState(), "inv-alice"),
    ).toHaveLength(1);

    // 下一次快照不再包含它 → 标记过期，不再可点。
    handleStructuredPayload({ ...EVENT_FIXTURES.snapshot, event_id: 41 });
    expect(useStructuredStore.getState().checks["chk-1"].status).toBe(
      "expired",
    );
    expect(
      pendingChecksFor(useStructuredStore.getState(), "inv-alice"),
    ).toHaveLength(0);
  });

  it("其他人可以看到待检定（只读）", () => {
    enableStructured();
    handleStructuredPayload(EVENT_FIXTURES.checkRequested);
    expect(visibleChecks(useStructuredStore.getState())).toHaveLength(1);
  });
});

describe("世界绑定", () => {
  it("切换世界清空请求、待检定与候选绑定", () => {
    enableStructured();
    sendStructuredAction({ kind: "move", destination_scene_id: "s1" });
    handleStructuredPayload(EVENT_FIXTURES.checkRequested);
    expect(activeRequests(useStructuredStore.getState())).toHaveLength(1);
    expect(Object.keys(useStructuredStore.getState().checks)).toHaveLength(1);

    rebindStructuredWorld("world-other");

    const state = useStructuredStore.getState();
    expect(state.identity.worldId).toBe("world-other");
    expect(activeRequests(state)).toHaveLength(0);
    expect(Object.keys(state.checks)).toHaveLength(0);
    expect(state.clues).toHaveLength(0);
    expect(state.items).toHaveLength(0);
    expect(state.destinations).toHaveLength(0);
  });

  it("旧世界迟到事件不污染新世界，也不改新世界 revision", () => {
    enableStructured();
    rebindStructuredWorld("world-other");
    const before = useStructuredStore.getState().identity.revision;

    const inbound = handleStructuredPayload({
      ...EVENT_FIXTURES.sceneChanged,
      world_id: WORLD_ID,
      event_id: 90,
    });
    expect(inbound.kind).toBe("foreign_world");
    expect(useStructuredStore.getState().identity.revision).toBe(before);
    expect(useStructuredStore.getState().destinations).toHaveLength(0);
  });

  it("切换世界后同 ID 请求可以重新提交（旧请求已清空）", () => {
    enableStructured();
    sendStructuredAction({ kind: "move", destination_scene_id: "s1" });
    rebindStructuredWorld("world-other");
    sent = [];
    // 新世界还没有 revision：明确拒绝，而不是拿旧世界版本号提交。
    const result = sendStructuredAction({
      kind: "move",
      destination_scene_id: "s2",
    });
    expect(result.ok).toBe(false);
    expect(sent).toHaveLength(0);
  });
});

describe("身份投影", () => {
  it("世界/调查员来自服务端快照，客户端不自行编造", () => {
    enableStructured();
    const identity = currentStructuredIdentity();
    expect(identity).toEqual({
      worldId: WORLD_ID,
      expectedRevision: 12,
      investigatorId: "inv-alice",
    });
  });

  it("世界来自 app-store 时也能工作（本地单机路径）", () => {
    useStructuredStore
      .getState()
      .applyCapabilities(HUMAN_ONLY_CAPABILITIES_WIRE);
    useStructuredStore.getState().bindWorld("", 0);
    useAppStore.setState({ activeWorldId: "local-world" });
    expect(currentStructuredIdentity()?.worldId).toBe("local-world");
  });

  it("房间里的调查员身份只认自己的认领，不用“当前行动者”", () => {
    // 房间的 activeInvestigatorId 是“轮到谁行动”，可能是别人的；用它当自己的
    // 身份会变成代别人掷骰。只认房间成员里我自己的认领（character_key）。
    useStructuredStore
      .getState()
      .applyCapabilities(HUMAN_ONLY_CAPABILITIES_WIRE);
    useStructuredStore.getState().bindWorld("room-world", 1);
    useOnlineStore.setState({
      activeInvestigatorId: "someone-elses-investigator",
      user: { id: "me", username: "me" },
      members: [
        {
          user_id: "me",
          username: "me",
          role: "player",
          investigator: {
            id: "claim-row-id",
            character_key: "default:me",
            status: "claimed",
          },
        },
        {
          user_id: "other",
          username: "other",
          role: "player",
          investigator: {
            id: "claim-row-2",
            character_key: "default:other",
            status: "claimed",
          },
        },
      ],
    });
    expect(currentStructuredIdentity()?.investigatorId).toBe("default:me");
    // 快照给了身份时以快照为准。
    useStructuredStore.setState((state) => ({
      ...state,
      identity: { ...state.identity, investigatorId: "snapshot:investigator" },
    }));
    expect(currentStructuredIdentity()?.investigatorId).toBe(
      "snapshot:investigator",
    );
  });

  it("游标暴露给调试与重连使用", () => {
    enableStructured();
    const cursor = structuredCursor();
    expect(cursor.worldId).toBe(WORLD_ID);
    expect(cursor.eventId).toBe(1);
    expect(cursor.revision).toBe(12);
  });
});
