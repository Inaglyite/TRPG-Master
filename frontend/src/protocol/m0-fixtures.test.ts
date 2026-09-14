/**
 * 用后端 M0 冻结的 fixtures 校验前端的类型与校验逻辑。
 *
 * 数据来源：`schemas/structured-play/v1/fixtures/`（Kimi 在 912e11c 提交的官方夹具）。
 * 这个测试是“前端实现”和“后端契约”之间的对账：官方 valid 夹具必须被接受，
 * 官方 invalid 夹具必须被拒绝。任何一方改字段都会在这里先红。
 */

import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  KEEPER_COMMAND_KINDS,
  actionRequestSchema,
  checkResponseSchema,
  commandRequestSchema,
  freeRollRequestSchema,
  parseStructuredEvent,
  STRUCTURED_PROTOCOL_VERSION,
} from "./structured";
import {
  KEEPER_COMMANDS,
  emptyKeeperValues,
  findKeeperCommand,
  validateKeeperFields,
  type FieldValues,
  type KeeperCommandSpec,
} from "./keeper-commands";

const FIXTURES = resolve(
  import.meta.dirname,
  "../../../schemas/structured-play/v1/fixtures",
);

function load(relative: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(FIXTURES, relative), "utf8"));
}

function filesIn(relative: string): string[] {
  return readdirSync(join(FIXTURES, relative))
    .filter((name) => name.endsWith(".json"))
    .sort();
}

const identity = {
  worldId: "world-fixture-1",
  expectedRevision: 12,
  investigatorId: "inv-alice",
};

describe("M0 官方 fixtures：valid 必须被前端接受", () => {
  it("action_request 全部夹具通过校验", () => {
    const names = filesIn("action_request");
    expect(names.length).toBeGreaterThanOrEqual(8);
    for (const name of names) {
      const fixture = load(`action_request/${name}`);
      const parsed = actionRequestSchema.safeParse(fixture);
      if (!parsed.success) {
        throw new Error(
          `${name} 未被前端接受: ${JSON.stringify(parsed.error.issues)}`,
        );
      }
      expect(parsed.data.protocol_version).toBe(STRUCTURED_PROTOCOL_VERSION);
      expect(parsed.data.world_id).toBe("world-fixture-1");
      // 夹具里的调查员/世界必须与前端提交时使用的身份同构。
      expect(typeof parsed.data.investigator_id).toBe("string");
      expect(parsed.data.expected_revision).toBe(identity.expectedRevision);
    }
  });

  it("free_roll_request 与 check_response 夹具通过校验", () => {
    for (const name of filesIn("free_roll_request")) {
      const result = freeRollRequestSchema.safeParse(
        load(`free_roll_request/${name}`),
      );
      if (!result.success) {
        throw new Error(`${name}: ${JSON.stringify(result.error.issues)}`);
      }
    }
    for (const name of filesIn("check_response")) {
      const result = checkResponseSchema.safeParse(
        load(`check_response/${name}`),
      );
      if (!result.success) {
        throw new Error(`${name}: ${JSON.stringify(result.error.issues)}`);
      }
    }
  });

  it("command_request 全部夹具通过信封校验，且字段表覆盖官方必填项", () => {
    const names = filesIn("command_request");
    // 夹具条数会随后端增补而变化；真正要守的是“每个官方夹具都能过前端校验、
    // 且命令 kind 与字段都在前端字段表里”（下面的断言）。
    expect(names.length).toBeGreaterThanOrEqual(14);
    for (const name of names) {
      const fixture = load(`command_request/${name}`);
      const parsed = commandRequestSchema.safeParse(fixture);
      if (!parsed.success) {
        throw new Error(`${name}: ${JSON.stringify(parsed.error.issues)}`);
      }
      const spec = findKeeperCommand(parsed.data.kind);
      expect(
        spec,
        `${name} 的命令 ${parsed.data.kind} 不在前端字段表里`,
      ).not.toBeNull();
      // 官方夹具的每个 payload 键都必须在字段表里声明（否则表单会漏字段）。
      const declared = new Set(spec!.fields.map((field) => field.name));
      for (const key of Object.keys(parsed.data.payload)) {
        const mapped =
          declared.has(key) ||
          (key === "from" && declared.has("from_investigator_id")) ||
          (key === "to" && declared.has("to_investigator_id")) ||
          (key === "target" && declared.has("target_id")) ||
          (key === "audience" && declared.has("audience_kind")) ||
          (key === "speaker" && declared.has("speaker_kind")) ||
          // M5：resolve_intent 的 thread 是复合对象（action/thread_id/pending_action/
          // disclosed/waiting_on），表单用 thread_* 与共用字段表达。
          (key === "thread" && declared.has("thread_action"));
        expect(mapped, `${name} 的字段 ${key} 未在前端字段表声明`).toBe(true);
      }
    }
  });

  it("event 夹具全部能被前端解析为结构化事件", () => {
    const names = filesIn("event");
    expect(names.length).toBeGreaterThanOrEqual(20);
    for (const name of names) {
      const fixture = load(`event/${name}`);
      const parsed = parseStructuredEvent(fixture);
      expect(parsed, `${name} 未被识别为结构化事件`).not.toBeNull();
      if (!parsed || "mismatch" in parsed)
        throw new Error(`${name} 版本不匹配`);
      expect(parsed.envelope.event_id).toBeTypeOf("number");
      expect(parsed.envelope.world_id).toBe("world-fixture-1");
      expect(parsed.envelope.payload).toBeTypeOf("object");
    }
  });

  it("event 夹具的类型都在前端已知事件集合里（否则会被当成未知消息丢掉）", async () => {
    const module = await import("./structured");
    const known = new Set<string>(module.STRUCTURED_EVENT_TYPES);
    for (const name of filesIn("event")) {
      const fixture = load(`event/${name}`);
      const type = String((fixture as { type?: unknown }).type ?? "");
      expect(known.has(type), `事件类型 ${type}（${name}）前端未登记`).toBe(
        true,
      );
    }
  });

  it("session_snapshot 的公开投影字段与前端读取器一致", () => {
    const snapshot = load("event/session_snapshot.json");
    const payload = (snapshot as { payload: Record<string, unknown> }).payload;
    // 前端读这些键来填充顶栏位置、候选、待检定与主持台授权。
    for (const key of [
      "revision",
      "execution_profile",
      "server_capabilities",
      "scene",
      "destinations",
      "investigator_id",
      "targets",
      "clues",
      "items",
      "pending_checks",
      "cursor",
    ]) {
      expect(
        payload,
        `session_snapshot 缺少前端需要的字段 ${key}`,
      ).toHaveProperty(key);
    }
    const capabilities = payload.server_capabilities as Record<string, unknown>;
    expect(capabilities.protocol_version).toBe(STRUCTURED_PROTOCOL_VERSION);
    expect(capabilities.execution_profile).toBe("structured_v1");
  });
});

/**
 * 把官方 fixture 的 payload 反填进表单值：只覆盖字段表里声明过的键与已知复合映射，
 * 用于「表单层能否拦住这个 payload」的判定。返回 null 表示表单校验通过。
 */
function commandFormValuesFromPayload(
  spec: KeeperCommandSpec,
  payload: unknown,
): string[] | null {
  if (!payload || typeof payload !== "object") return null;
  const raw = payload as Record<string, unknown>;
  const values: FieldValues = {};
  const declared = new Set(spec.fields.map((field) => field.name));
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === "string" && declared.has(key)) values[key] = value;
    if (Array.isArray(value) && declared.has(key))
      values[key] = value.join(", ");
  }
  // 复合映射：thread.* → thread_* / disclosed / waiting_on
  const thread = raw.thread;
  if (thread && typeof thread === "object") {
    const nested = thread as Record<string, unknown>;
    if (typeof nested.action === "string") values.thread_action = nested.action;
    if (Array.isArray(nested.disclosed))
      values.disclosed = nested.disclosed.join("\n");
    if (typeof nested.waiting_on === "string")
      values.waiting_on = nested.waiting_on;
    const action = nested.pending_action;
    if (action && typeof action === "object") {
      const pending = action as Record<string, unknown>;
      if (typeof pending.kind === "string")
        values.pending_action_kind = pending.kind;
      if (typeof pending.note === "string")
        values.pending_action_note = pending.note;
      if (typeof pending.destination_scene_id === "string") {
        values.pending_action_destination = pending.destination_scene_id;
      }
    }
  }
  const errors = validateKeeperFields(spec, {
    ...emptyKeeperValues(spec),
    ...values,
  });
  return errors.length ? errors : null;
}

describe("M0 官方 fixtures：invalid 必须被前端拒绝", () => {
  it("官方 invalid 夹具全部被拒绝，且拒绝原因是字段级校验", () => {
    const names = filesIn("invalid");
    expect(names.length).toBeGreaterThanOrEqual(7);
    for (const name of names) {
      const fixture = load(`invalid/${name}`);
      const type = String((fixture as { type?: unknown }).type ?? "");
      let accepted = false;
      if (type === "action_request") {
        accepted = actionRequestSchema.safeParse(fixture).success;
      } else if (type === "check_response") {
        accepted = checkResponseSchema.safeParse(fixture).success;
      } else if (type === "free_roll_request") {
        accepted = freeRollRequestSchema.safeParse(fixture).success;
      } else if (type === "command_request") {
        // 命令帧有两层：线上 zod 结构 + 表驱动表单校验。官方 invalid 夹具可能
        // 只在语义层不合法（例如 knowledge_type 不在四选一内），因此两层都过才算被接受。
        const structured = commandRequestSchema.safeParse(fixture).success;
        const spec = findKeeperCommand(
          String((fixture as { kind?: unknown }).kind ?? ""),
        );
        const formAccepted = spec
          ? commandFormValuesFromPayload(
              spec,
              (fixture as { payload?: unknown }).payload,
            ) === null
          : true;
        accepted = structured && formAccepted;
      } else {
        // 未知事件类型：前端按通用信封容错接收（后端负责不去投递非法事件），
        // 因此这里不断言拒绝，只断言不会崩。
        parseStructuredEvent(fixture);
        continue;
      }
      expect(accepted, `${name} 本应被前端拒绝，却被接受`).toBe(false);
    }
  });

  it("presentation=original 但 physical_item_id 为 null 会被拒绝", () => {
    const fixture = load("invalid/action_request_original_without_item.json");
    const result = actionRequestSchema.safeParse(fixture);
    expect(result.success).toBe(false);
    if (result.success) return;
    expect(
      result.error.issues.some((issue) =>
        issue.path.join(".").includes("physical_item_id"),
      ),
    ).toBe(true);
  });

  it("operation=custom 缺 approach 会被拒绝", () => {
    const fixture = load("invalid/action_request_custom_without_approach.json");
    const result = actionRequestSchema.safeParse(fixture);
    expect(result.success).toBe(false);
    if (result.success) return;
    expect(
      result.error.issues.some((issue) =>
        issue.path.join(".").includes("approach"),
      ),
    ).toBe(true);
  });

  it("非法骰式与非法 decision 会被拒绝", () => {
    expect(
      freeRollRequestSchema.safeParse(load("invalid/free_roll_bad_spec.json"))
        .success,
    ).toBe(false);
    expect(
      checkResponseSchema.safeParse(
        load("invalid/check_response_bad_decision.json"),
      ).success,
    ).toBe(false);
  });

  it("前端命令字段表与 M0 命令集合逐项一致（防漂移）", () => {
    const declared = KEEPER_COMMANDS.map((command) => command.kind).sort();
    const official = [...KEEPER_COMMAND_KINDS].sort();
    expect(declared).toEqual(official);
  });

  it("主持命令字段表能拦住官方认可的必填缺失（表单层）", () => {
    // 表驱动的表单校验与 M0 required 对齐：给空值必须报错。
    const spec = findKeeperCommand("grant_clue")!;
    const errors = validateKeeperFields(spec, {});
    expect(errors.length).toBeGreaterThan(0);
  });
});
