import { describe, expect, it } from "vitest";
import { conditionPayloadSchema } from "./conditions";
import { buildCommandRequest } from "./structured";
import {
  buildKeeperPayload,
  emptyKeeperValues,
  findKeeperCommand,
  validateKeeperFields,
} from "./keeper-commands";
const spec = findKeeperCommand("record_condition")!;
const valid = {
  investigator_id: "inv-alice",
  condition: "unconscious",
  operation: "remove",
  expected_present: true,
  basis: "急救已经结算，主持确认恢复意识。",
};
describe("人物状态记录的严格契约", () => {
  it("所有五字段必填，false是已核对的不存在，未知前值不默认成false", () => {
    expect(
      spec.fields.filter((field) => field.required).map((field) => field.name),
    ).toEqual(Object.keys(valid));
    const empty = emptyKeeperValues(spec);
    expect(empty.expected_present).toBe("");
    expect(validateKeeperFields(spec, empty).join(" ")).toContain("核对");
    expect(buildKeeperPayload(spec, empty).expected_present).toBeNull();
    const payload = buildKeeperPayload(spec, {
      ...valid,
      operation: "add",
      expected_present: false,
    });
    expect(payload.expected_present).toBe(false);
    expect(
      buildCommandRequest("record_condition", payload, {
        worldId: "world",
        expectedRevision: 1,
      }).ok,
    ).toBe(true);
  });
  it.each([0, 1, "false", "true", null, undefined])(
    "前值不强制转换：%s",
    (expected_present) => {
      expect(
        conditionPayloadSchema.safeParse({ ...valid, expected_present })
          .success,
      ).toBe(false);
    },
  );
  it.each([
    { hp: 99 },
    { condition: "healthy" },
    { operation: "heal" },
    { basis: "  " },
  ])("拒绝补血/未知状态/未知操作/无依据：%s", (over) => {
    expect(
      buildCommandRequest(
        "record_condition",
        { ...valid, ...over },
        { worldId: "world", expectedRevision: 1 },
      ).ok,
    ).toBe(false);
  });
  it("解除死亡在表单预检拒绝", () => {
    expect(
      validateKeeperFields(spec, { ...valid, condition: "dead" }).join(" "),
    ).toContain("死亡");
  });
});
