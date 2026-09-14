import { describe, expect, it } from "vitest";

import {
  KEEPER_COMMANDS,
  buildKeeperPayload,
  emptyKeeperValues,
  findKeeperCommand,
  validateKeeperFields,
  type FieldValues,
} from "./keeper-commands";

function fill(kind: string, values: FieldValues): FieldValues {
  const spec = findKeeperCommand(kind)!;
  return { ...emptyKeeperValues(spec), ...values };
}

describe("主持命令字段表（对照 M0 command_request.json）", () => {
  it("覆盖 M0 定义的全部命令，且不发明命令", () => {
    expect(KEEPER_COMMANDS.map((command) => command.kind).sort()).toEqual(
      [
        "adjust_stat",
        "advance_time",
        "grant_clue",
        "move_party",
        "present_handout",
        "present_information",
        "publish_message",
        "record_fact",
        "record_memory",
        "request_check",
        "resolve_draft",
        "resolve_check",
        "resolve_intent",
        "set_npc_presence",
        "transfer_item",
        "use_item",
      ].sort(),
    );
  });

  it("必填字段与 M0 required 一致", () => {
    const required = (kind: string) =>
      findKeeperCommand(kind)!
        .fields.filter((field) => field.required)
        .map((field) => field.name)
        .filter(
          (name) =>
            !name.startsWith("audience_") && !name.startsWith("target_"),
        );
    expect(required("grant_clue")).toEqual([
      "clue_id",
      "recipient_investigator_ids",
      "basis",
    ]);
    expect(required("adjust_stat")).toEqual([
      "investigator_id",
      "field",
      "delta",
      "reason",
    ]);
    expect(required("advance_time")).toEqual(["minutes", "reason"]);
    expect(required("move_party")).toEqual(["destination_scene_id"]);
    expect(required("resolve_intent")).toEqual(["request_id", "resolution"]);
    expect(required("request_check")).toEqual([
      "investigator_id",
      "skill",
      "difficulty",
      "attempt",
      "visibility",
    ]);
    expect(required("transfer_item")).toEqual([
      "item_id",
      "quantity",
      "from_investigator_id",
      "to_investigator_id",
    ]);
  });

  it("数值边界与 M0 一致（奖惩骰 ±2、数量 1-999、时间上限）", () => {
    const field = (kind: string, name: string) =>
      findKeeperCommand(kind)!.fields.find((entry) => entry.name === name)!;
    expect(field("request_check", "bonus_penalty")).toMatchObject({
      min: -2,
      max: 2,
    });
    expect(field("use_item", "quantity")).toMatchObject({ min: 1, max: 999 });
    expect(field("advance_time", "minutes")).toMatchObject({
      min: 0,
      max: 10080,
    });
    expect(field("move_party", "travel_minutes")).toMatchObject({
      min: 0,
      max: 1440,
    });
    expect(field("adjust_stat", "delta")).toMatchObject({ min: -99, max: 99 });
    expect(field("adjust_stat", "field").enumValues).toEqual([
      "hp",
      "san",
      "max_hp",
      "max_san",
    ]);
  });
});

describe("主持表单 → M0 payload", () => {
  it("定向发放线索按接收者记录知情，并可同时展示素材", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("grant_clue")!,
      fill("grant_clue", {
        clue_id: "clue_death_certificate",
        recipient_investigator_ids: "inv-alice, inv-bob",
        basis: "医生当面说明",
        present_asset_id: "asset_cert",
        note: "",
      }),
    );
    expect(payload).toEqual({
      clue_id: "clue_death_certificate",
      recipient_investigator_ids: ["inv-alice", "inv-bob"],
      basis: "医生当面说明",
      present_asset_id: "asset_cert",
    });
  });

  it("NPC 发言使用 M0 speaker 形态（kind + id），不使用旧 type 字段", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("publish_message")!,
      fill("publish_message", {
        speaker_kind: "npc",
        speaker_id: "john_whitcroft",
        audience_kind: "public",
        text: "停尸房不对外开放。",
        in_character: true,
      }),
    );
    expect(payload.speaker).toEqual({ kind: "npc", id: "john_whitcroft" });
    expect(payload.audience).toEqual({ kind: "public" });
    expect(payload.text).toBe("停尸房不对外开放。");
    expect(payload.in_character).toBe(true);
    expect(payload).not.toHaveProperty("speaker.type");
  });

  it("接收范围“指定调查员”生成 investigators audience", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("publish_message")!,
      fill("publish_message", {
        speaker_kind: "keeper",
        audience_kind: "investigators",
        audience_investigator_ids: "inv-alice",
        text: "只有你能看到这句。",
      }),
    );
    expect(payload.audience).toEqual({
      kind: "investigators",
      investigator_ids: ["inv-alice"],
    });
    expect(payload.speaker).toEqual({ kind: "keeper" });
  });

  it("HP/SAN 调整带原因；delta 允许负数", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("adjust_stat")!,
      fill("adjust_stat", {
        investigator_id: "inv-alice",
        field: "san",
        delta: "-3",
        reason: "目击尸体",
      }),
    );
    expect(payload).toEqual({
      investigator_id: "inv-alice",
      field: "san",
      delta: -3,
      reason: "目击尸体",
    });
  });

  it("物品转移的 from/to 用 {kind:'investigator', id} 形态", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("transfer_item")!,
      fill("transfer_item", {
        item_id: "item_bandage",
        quantity: "2",
        from_investigator_id: "inv-alice",
        to_investigator_id: "inv-bob",
      }),
    );
    expect(payload).toEqual({
      item_id: "item_bandage",
      quantity: 2,
      from: { kind: "investigator", id: "inv-alice" },
      to: { kind: "investigator", id: "inv-bob" },
    });
  });

  it("处理玩家意图带 resolution/outcome；未填的可选字段不出现", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("resolve_intent")!,
      fill("resolve_intent", {
        request_id: "req-1",
        resolution: "completed",
        outcome: "success",
        note: "",
      }),
    );
    expect(payload).toEqual({
      request_id: "req-1",
      resolution: "completed",
      outcome: "success",
    });
    expect(payload).not.toHaveProperty("note");
  });

  it("记录事实使用 audience 形态与可选 source", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("record_fact")!,
      fill("record_fact", {
        text: "停尸房钥匙在值班室。",
        audience_kind: "public",
        source: "ruling",
      }),
    );
    expect(payload).toEqual({
      text: "停尸房钥匙在值班室。",
      audience: { kind: "public" },
      source: "ruling",
    });
  });

  it("检定请求带 attempt/visibility；奖惩骰与代价可选", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("request_check")!,
      fill("request_check", {
        investigator_id: "inv-alice",
        skill: "说服",
        difficulty: "regular",
        bonus_penalty: "1",
        attempt: "向医生说明来意",
        known_cost: "可能需要出示证件",
        visibility: "public",
      }),
    );
    expect(payload).toEqual({
      investigator_id: "inv-alice",
      skill: "说服",
      difficulty: "regular",
      bonus_penalty: 1,
      attempt: "向医生说明来意",
      known_cost: "可能需要出示证件",
      visibility: "public",
    });
  });
});

describe("主持表单校验", () => {
  it("缺必填字段时报出字段名，且不产生 payload", () => {
    const spec = findKeeperCommand("grant_clue")!;
    const errors = validateKeeperFields(spec, emptyKeeperValues(spec));
    expect(errors.join("")).toContain("线索");
    expect(errors.join("")).toContain("接收调查员");
    expect(errors.join("")).toContain("依据");
  });

  it("数值越界在本地就拦下（奖惩骰 / 数量 / 时间）", () => {
    const bonus = validateKeeperFields(
      findKeeperCommand("request_check")!,
      fill("request_check", {
        investigator_id: "i",
        skill: "s",
        difficulty: "regular",
        bonus_penalty: "5",
        attempt: "a",
        visibility: "public",
      }),
    );
    expect(bonus.join("")).toContain("奖惩骰");

    const quantity = validateKeeperFields(
      findKeeperCommand("use_item")!,
      fill("use_item", {
        investigator_id: "i",
        item_id: "item",
        quantity: "0",
        operation: "apply",
      }),
    );
    expect(quantity.join("")).toContain("数量");

    const minutes = validateKeeperFields(
      findKeeperCommand("advance_time")!,
      fill("advance_time", { minutes: "20000", reason: "等待" }),
    );
    expect(minutes.join("")).toContain("分钟");
  });

  it("指定调查员范围必须选人", () => {
    const errors = validateKeeperFields(
      findKeeperCommand("publish_message")!,
      fill("publish_message", {
        speaker_kind: "keeper",
        audience_kind: "investigators",
        text: "私密留言",
      }),
    );
    expect(errors.join("")).toContain("至少一名调查员");
  });

  it("文本超长被拦下", () => {
    const errors = validateKeeperFields(
      findKeeperCommand("advance_time")!,
      fill("advance_time", { minutes: "30", reason: "x".repeat(260) }),
    );
    expect(errors.join("")).toContain("200");
  });

  it("等待玩家回应：待办写成 pending_action + disclosed，不塞进自由文本", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("resolve_intent")!,
      fill("resolve_intent", {
        request_id: "req-1",
        resolution: "awaiting_player",
        pending_action_kind: "move",
        pending_action_note: "尚未出发前往停尸房",
        pending_action_destination: "morgue",
        disclosed: "停尸房需要值班医生放行\n现在联系不上医生",
        note: "等玩家决定是否现在联系",
      }),
    );
    expect(payload).toEqual({
      request_id: "req-1",
      resolution: "awaiting_player",
      note: "等玩家决定是否现在联系",
      pending_action: {
        kind: "move",
        note: "尚未出发前往停尸房",
        destination_scene_id: "morgue",
      },
      disclosed: ["停尸房需要值班医生放行", "现在联系不上医生"],
    });
  });

  it("等待玩家回应但没写「尚未执行什么」要被拦下", () => {
    const errors = validateKeeperFields(
      findKeeperCommand("resolve_intent")!,
      fill("resolve_intent", {
        request_id: "req-1",
        resolution: "awaiting_player",
      }),
    );
    expect(errors.join("")).toContain("尚未执行什么");
  });

  it("普通收尾不携带 pending_action/disclosed", () => {
    const payload = buildKeeperPayload(
      findKeeperCommand("resolve_intent")!,
      fill("resolve_intent", {
        request_id: "req-1",
        resolution: "completed",
        outcome: "success",
      }),
    );
    expect(payload).toEqual({
      request_id: "req-1",
      resolution: "completed",
      outcome: "success",
    });
  });

  it("转移物品必须选来源与去向", () => {
    const errors = validateKeeperFields(
      findKeeperCommand("transfer_item")!,
      fill("transfer_item", { item_id: "item", quantity: "1" }),
    );
    expect(errors.join("")).toContain("来源");
    expect(errors.join("")).toContain("去向");
  });

  it("合法输入没有错误", () => {
    expect(
      validateKeeperFields(
        findKeeperCommand("advance_time")!,
        fill("advance_time", { minutes: "30", reason: "驱车前往医学院" }),
      ),
    ).toEqual([]);
  });
});
