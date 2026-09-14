import { describe, expect, it } from "vitest";

import {
  STRUCTURED_PROTOCOL_VERSION,
  StructuredEventSequencer,
  actionDigest,
  buildActionRequest,
  buildCheckResponse,
  buildCommandRequest,
  buildFreeRollRequest,
  freeRollReason,
  parseStructuredEvent,
  readServerCapabilities,
  requestErrorText,
  type ServerCapabilities,
} from "./structured";
import {
  HUMAN_ONLY_CAPABILITIES,
  LEGACY_CAPABILITIES,
  STRUCTURED_CAPABILITIES,
} from "./structured-fixtures";

const IDENTITY = {
  worldId: "world-1",
  expectedRevision: 12,
  investigatorId: "inv-alice",
};

describe("结构化请求构造", () => {
  it("按 §5.1 生成 action_request：ID 与枚举是权威字段，文本只在补充字段", () => {
    const built = buildActionRequest(
      {
        kind: "present_clue",
        clue_id: "clue_death_certificate",
        presentation: "original",
        physical_item_id: "item_death_certificate",
        target: { kind: "npc", id: "john_whitcroft" },
        question: "你认得这份证明吗？",
      },
      IDENTITY,
      "req-fixed-1",
    );

    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request).toEqual({
      type: "action_request",
      protocol_version: STRUCTURED_PROTOCOL_VERSION,
      request_id: "req-fixed-1",
      world_id: "world-1",
      expected_revision: 12,
      investigator_id: "inv-alice",
      action: {
        kind: "present_clue",
        clue_id: "clue_death_certificate",
        presentation: "original",
        physical_item_id: "item_death_certificate",
        target: { kind: "npc", id: "john_whitcroft" },
        question: "你认得这份证明吗？",
      },
    });
    // 权威路径里不允许出现自然语言行动句。
    expect(JSON.stringify(built.request)).not.toContain("我向");
  });

  it("使用道具带物品 ID、数量与操作；不在前端决定扣减", () => {
    const built = buildActionRequest(
      {
        kind: "use_item",
        item_id: "item_bandage",
        quantity: 2,
        operation: "apply",
        target: { kind: "investigator", id: "inv-bob" },
        approach: "先清创再包扎",
      },
      IDENTITY,
      "req-fixed-2",
    );
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request.action).toMatchObject({
      kind: "use_item",
      item_id: "item_bandage",
      quantity: 2,
      operation: "apply",
    });
    // 请求里没有“剩余数量”这类客户端自算字段。
    expect(built.request.action).not.toHaveProperty("remaining");
  });

  it("未解析目标允许提交文本，供主持澄清；不强行绑定某个 NPC", () => {
    const built = buildActionRequest(
      {
        kind: "present_clue",
        clue_id: "c1",
        presentation: "describe",
        physical_item_id: null,
        target: { kind: "unresolved", text: "站在门边的那位" },
      },
      IDENTITY,
      "req-fixed-3",
    );
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request.action).toMatchObject({
      target: { kind: "unresolved", text: "站在门边的那位" },
    });
  });

  it("缺少世界/调查员/版本号时拒绝构造，并给出可读原因", () => {
    const noWorld = buildActionRequest(
      { kind: "move", destination_scene_id: "s1" },
      { ...IDENTITY, worldId: "" },
    );
    expect(noWorld).toEqual({
      ok: false,
      reason: "还没有进入世界，无法提交结构化请求。",
    });

    const noInvestigator = buildActionRequest(
      { kind: "move", destination_scene_id: "s1" },
      { ...IDENTITY, investigatorId: "" },
    );
    expect(noInvestigator.ok).toBe(false);

    const noRevision = buildActionRequest(
      { kind: "move", destination_scene_id: "s1" },
      { ...IDENTITY, expectedRevision: -1 },
    );
    expect(noRevision.ok).toBe(false);
  });

  it("非法枚举/空 ID 被 schema 拦下", () => {
    const badPresentation = buildActionRequest(
      {
        kind: "present_clue",
        // @ts-expect-error 故意传非法枚举，验证运行时校验
        presentation: "give-away",
        clue_id: "c1",
        physical_item_id: null,
        target: { kind: "npc", id: "npc-1" },
      },
      IDENTITY,
    );
    expect(badPresentation.ok).toBe(false);

    const badTarget = buildActionRequest(
      {
        kind: "present_clue",
        clue_id: "c1",
        presentation: "describe",
        physical_item_id: null,
        target: { kind: "npc", id: "" },
      },
      IDENTITY,
    );
    expect(badTarget.ok).toBe(false);

    const badQuantity = buildActionRequest(
      {
        kind: "use_item",
        item_id: "i1",
        quantity: 0,
        operation: "apply",
      },
      IDENTITY,
    );
    expect(badQuantity.ok).toBe(false);
  });

  it("检定回应只带 check_request_id 与掷骰/放弃，不带技能值或难度", () => {
    const built = buildCheckResponse("chk-1", "roll", IDENTITY, "req-chk-1");
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request).toEqual({
      type: "check_response",
      protocol_version: STRUCTURED_PROTOCOL_VERSION,
      request_id: "req-chk-1",
      world_id: "world-1",
      check_request_id: "chk-1",
      decision: "roll",
    });
    expect(Object.keys(built.request)).not.toContain("skill");
    expect(Object.keys(built.request)).not.toContain("difficulty");
  });

  it("主持命令信封带 command_id 与 expected_revision", () => {
    const built = buildCommandRequest(
      "adjust_stat",
      { investigator_id: "inv-alice", field: "san", delta: -3, reason: "目击" },
      IDENTITY,
      "cmd-1",
    );
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request.command_id).toBe("cmd-1");
    expect(built.request.expected_revision).toBe(12);
    expect(built.request.kind).toBe("adjust_stat");
  });

  it("普通掷骰使用受限表达式", () => {
    expect(freeRollReason("1d100")).toBeNull();
    expect(freeRollReason("2d6+1")).toBeNull();
    expect(freeRollReason("0d6")).toContain("骰子个数");
    expect(freeRollReason("40d6")).toContain("骰子个数");
    expect(freeRollReason("1d1")).toContain("骰面");
    expect(freeRollReason("d100")).toContain("格式");

    const built = buildFreeRollRequest("1d100", IDENTITY, "req-roll-1");
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request).toEqual({
      type: "free_roll_request",
      protocol_version: STRUCTURED_PROTOCOL_VERSION,
      request_id: "req-roll-1",
      world_id: "world-1",
      investigator_id: "inv-alice",
      spec: "1d100",
    });
  });

  it("载荷摘要忽略 request_id：同内容重发摘要一致，改内容摘要变化", () => {
    const first = buildActionRequest(
      { kind: "move", destination_scene_id: "s1" },
      IDENTITY,
      "req-a",
    );
    const second = buildActionRequest(
      { kind: "move", destination_scene_id: "s1" },
      IDENTITY,
      "req-b",
    );
    const different = buildActionRequest(
      { kind: "move", destination_scene_id: "s2" },
      IDENTITY,
      "req-a",
    );
    expect(first.ok && second.ok && different.ok).toBe(true);
    if (!first.ok || !second.ok || !different.ok) return;
    expect(
      actionDigest(first.request as unknown as Record<string, unknown>),
    ).toBe(actionDigest(second.request as unknown as Record<string, unknown>));
    expect(
      actionDigest(first.request as unknown as Record<string, unknown>),
    ).not.toBe(
      actionDigest(different.request as unknown as Record<string, unknown>),
    );
  });
});

describe("能力协商", () => {
  it("structured_v1 + 版本匹配才开启；缺字段一律 fail-closed", () => {
    expect(LEGACY_CAPABILITIES.structuredProtocol).toBe(false);
    expect(STRUCTURED_CAPABILITIES.structuredProtocol).toBe(true);
    expect(STRUCTURED_CAPABILITIES.checkRequest).toBe(true);
    expect(STRUCTURED_CAPABILITIES.useItem).toBe(true);

    // 声明了 structured 但版本不匹配 → 不开
    expect(
      readServerCapabilities({
        structured_protocol: true,
        execution_profile: "structured_v1",
        protocol_version: 99,
      }).structuredProtocol,
    ).toBe(false);
    // 只有 execution_profile 是 legacy → 不开
    expect(
      readServerCapabilities({
        structured_protocol: true,
        execution_profile: "legacy",
        protocol_version: STRUCTURED_PROTOCOL_VERSION,
      }).structuredProtocol,
    ).toBe(false);
    // 空对象/垃圾值 → 全 false
    const junk = readServerCapabilities({ structured_protocol: "yes" });
    expect(junk.structuredProtocol).toBe(false);
    expect(junk.keeperConsole).toBe(false);
    expect(readServerCapabilities(null).structuredProtocol).toBe(false);
  });

  it("命令列表能替代显式布尔开关，但不能绕过 structured 总开关", () => {
    const fromCommands = readServerCapabilities({
      structured_protocol: true,
      execution_profile: "structured_v1",
      protocol_version: STRUCTURED_PROTOCOL_VERSION,
      commands: ["move_party", "use_item"],
    });
    expect(fromCommands.moveAction).toBe(true);
    expect(fromCommands.useItem).toBe(true);
    expect(fromCommands.presentClue).toBe(false);

    const legacyWithCommands = readServerCapabilities({
      execution_profile: "legacy",
      commands: ["move_party"],
    });
    expect(legacyWithCommands.moveAction).toBe(false);
  });

  it("human-only 服务端不暴露 assisted/agent 能力", () => {
    expect(HUMAN_ONLY_CAPABILITIES.keeperModes).toEqual(["human"]);
    expect(HUMAN_ONLY_CAPABILITIES.assistedDraft).toBe(false);
    expect(HUMAN_ONLY_CAPABILITIES.agentTakeover).toBe(false);
    expect(HUMAN_ONLY_CAPABILITIES.keeperConsole).toBe(true);
  });

  it("未知错误码用服务端文案兜底，不显示空白", () => {
    expect(requestErrorText("revision_conflict")).toContain("世界状态已更新");
    expect(requestErrorText("code_from_future", "服务端新增原因")).toBe(
      "服务端新增原因",
    );
    expect(
      requestErrorText("revision_conflict", "世界版本已从 12 变为 13"),
    ).toContain("世界版本已从 12 变为 13");
    expect(requestErrorText(undefined)).toBe("请求被拒绝，原因未知。");
  });
});

describe("事件解析与游标", () => {
  const envelope = (over: Record<string, unknown> = {}) => ({
    protocol_version: STRUCTURED_PROTOCOL_VERSION,
    event_id: 5,
    world_id: "world-1",
    sequence: 5,
    revision: 12,
    type: "action_status",
    payload: { request_id: "r1", status: "processing" },
    ...over,
  });

  it("旧协议消息不会被当成结构化事件", () => {
    expect(
      parseStructuredEvent({ type: "narrative_chunk", text: "x" }),
    ).toBeNull();
    expect(parseStructuredEvent("nope")).toBeNull();
    expect(parseStructuredEvent(null)).toBeNull();
    // 有 event_id 但缺 world/revision → 仍不是结构化事件
    expect(parseStructuredEvent({ event_id: 1, type: "x" })).toBeNull();
  });

  it("协议版本不同时返回 mismatch，而不是解析失败", () => {
    const parsed = parseStructuredEvent(envelope({ protocol_version: 7 }));
    expect(parsed).toEqual({ mismatch: true });
  });

  it("未知事件类型保留为通用信封，交给上层忽略而不是丢掉会话", () => {
    const parsed = parseStructuredEvent(
      envelope({ type: "future_event_type" }),
    );
    expect(parsed).not.toBeNull();
    if (!parsed || "mismatch" in parsed) return;
    expect(parsed.envelope.type).toBe("future_event_type");
    expect(parsed.envelope.payload).toEqual({
      request_id: "r1",
      status: "processing",
    });
  });

  it("缺 payload 时给出空对象，不返回 undefined", () => {
    const parsed = parseStructuredEvent(envelope({ payload: undefined }));
    if (!parsed || "mismatch" in parsed) throw new Error("expected envelope");
    expect(parsed.envelope.payload).toEqual({});
  });

  it("按 event_id 去重：同 revision 的不同事件全部保留", () => {
    const sequencer = new StructuredEventSequencer();
    const first = sequencer.accept({
      world_id: "w1",
      event_id: 1,
      revision: 12,
      sequence: 1,
    });
    // 同一 revision 的第二条不同事件必须被应用，不能因为 revision 相同丢弃。
    const second = sequencer.accept({
      world_id: "w1",
      event_id: 2,
      revision: 12,
      sequence: 2,
    });
    const repeated = sequencer.accept({
      world_id: "w1",
      event_id: 2,
      revision: 12,
      sequence: 2,
    });
    expect([first, second, repeated]).toEqual(["apply", "apply", "duplicate"]);

    const older = sequencer.accept({
      world_id: "w1",
      event_id: 1,
      revision: 13,
      sequence: 9,
    });
    expect(older).toBe("duplicate");
  });

  it("世界变更重绑：旧世界事件判为 foreign_world，不会污染新世界", () => {
    const sequencer = new StructuredEventSequencer();
    expect(sequencer.accept({ world_id: "w1", event_id: 1, revision: 1 })).toBe(
      "apply",
    );
    sequencer.rebindWorld("w2");
    expect(sequencer.boundWorldId).toBe("w2");
    expect(sequencer.accept({ world_id: "w1", event_id: 2, revision: 2 })).toBe(
      "foreign_world",
    );
    expect(sequencer.accept({ world_id: "w2", event_id: 1, revision: 1 })).toBe(
      "apply",
    );
  });

  it("首连未绑定世界时采纳第一条事件的世界", () => {
    const sequencer = new StructuredEventSequencer();
    expect(sequencer.accept({ world_id: "w9", event_id: 4, revision: 3 })).toBe(
      "apply",
    );
    expect(sequencer.boundWorldId).toBe("w9");
  });

  it("游标拒绝事件号倒退，即使 revision 更大", () => {
    const sequencer = new StructuredEventSequencer();
    sequencer.accept({ world_id: "w1", event_id: 10, revision: 1 });
    expect(sequencer.accept({ world_id: "w1", event_id: 9, revision: 5 })).toBe(
      "duplicate",
    );
    expect(sequencer.cursor.revision).toBe(1);
  });
});

describe("capability 类型与常量", () => {
  it("能力对象是不可变读取的普通对象", () => {
    const caps: ServerCapabilities = STRUCTURED_CAPABILITIES;
    expect(Object.isFrozen(caps)).toBe(false);
    expect(caps.protocolVersion).toBe(STRUCTURED_PROTOCOL_VERSION);
    expect(caps.executionProfile).toBe("structured_v1");
  });
});
