import { beforeEach, describe, expect, it } from "vitest";
import { initialStructuredState, useStructuredStore } from "./structured-store";

const roll = {
  roll_id: "roll-new",
  investigator_id: "alice",
  actor_id: "alice",
  target_id: "guard",
  action_type: "firearm",
  source: "action",
};
const combat = { active: true, awaiting_roll: true, participants: [] };
function event(type: string, payload: Record<string, unknown>) {
  useStructuredStore.getState().applyEvent({
    event_id: 1,
    world_id: "battle",
    revision: 5,
    type,
    payload,
  });
}
beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));

describe("战斗投影恢复与终态", () => {
  it("结果按roll_id去重，刷新恢复且换世界清空", () => {
    const result = {
      roll_id: "roll-done",
      investigator_id: "alice",
      encounter_id: "enc-1",
      round: 1,
      actor_id: "alice",
      target_id: "guard",
      action_type: "firearm",
      response: "roll",
      outcome: "miss",
      rolls: [{ actor_id: "alice", role: "attack", roll: 99, level: "失败" }],
      damage: null,
    };
    event("combat_roll_resolved", {
      roll_id: result.roll_id,
      response: "roll",
      result,
    });
    event("combat_roll_resolved", {
      roll_id: result.roll_id,
      response: "roll",
      result,
    });
    expect(useStructuredStore.getState().combatResults).toEqual([result]);
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 5, combat_results: [result] }, "battle");
    expect(useStructuredStore.getState().combatResults).toEqual([result]);
    useStructuredStore.getState().bindWorld("other-world");
    expect(useStructuredStore.getState().combatResults).toEqual([]);
  });
  it("快照恢复待掷骰而不执行它", () => {
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 4, combat, combat_roll: roll }, "battle");
    expect(useStructuredStore.getState().combatRoll).toEqual(roll);
    expect(useStructuredStore.getState().requestOrder).toEqual([]);
  });
  it("旧 nonce 的结算事件不能清除新的待掷骰", () => {
    event("combat_roll_required", roll);
    event("combat_roll_resolved", { roll_id: "roll-old", response: "roll" });
    expect(useStructuredStore.getState().combatRoll?.roll_id).toBe("roll-new");
    event("combat_roll_resolved", { roll_id: "roll-new", response: "roll" });
    expect(useStructuredStore.getState().combatRoll).toBeNull();
  });
  it("停止战斗撤销待办，不从叙述猜 HP", () => {
    event("combat_roll_required", roll);
    event("combat_updated", {
      active: false,
      outcome: "stalemate",
      participants: [],
    });
    expect(useStructuredStore.getState().combatRoll).toBeNull();
    expect(useStructuredStore.getState().combat?.outcome).toBe("stalemate");
  });
  it("缺少字段的快照清除旧世界战斗，不复活按钮", () => {
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 4, combat, combat_roll: roll }, "battle");
    useStructuredStore.getState().applySnapshot({ revision: 1 }, "other-world");
    expect(useStructuredStore.getState().combat).toBeNull();
    expect(useStructuredStore.getState().combatRoll).toBeNull();
    expect(useStructuredStore.getState().caseSettlements).toEqual([]);
  });
  it("非法投影不进入状态", () => {
    event("combat_roll_required", { ...roll, investigator_id: 17 });
    expect(useStructuredStore.getState().combatRoll).toBeNull();
  });
  it("结局投影撤销所有可操作的旧决定", () => {
    event("combat_roll_required", roll);
    event("game_ended", {
      id: "good",
      type: "good",
      title: "真相浮现",
      summary: "结束",
    });
    expect(useStructuredStore.getState().combatRoll).toBeNull();
    expect(useStructuredStore.getState().combatDecision).toBeNull();
    expect(useStructuredStore.getState().gameOver?.title).toBe("真相浮现");
  });
});
