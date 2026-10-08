import { beforeEach, expect, it } from "vitest";
import { parseServerMessage } from "../protocol/server-message";
import { type StructuredEventEnvelope } from "../protocol/structured";
import {
  buildKeeperPayload,
  emptyKeeperValues,
  findKeeperCommand,
  validateKeeperFields,
} from "../protocol/keeper-commands";
import { initialStructuredState, useStructuredStore } from "./structured-store";

const progress = {
  clues: [],
  clocks: [
    {
      id: "danger",
      title: "秘密危险",
      value: 1,
      max: 6,
      level: "秘密征兆",
      next_level: "",
      advance_when: [],
    },
  ],
};
const event = {
  protocol_version: 1,
  world_id: "world",
  event_id: 1,
  sequence: 1,
  revision: 1,
  type: "keeper_progress_updated",
  payload: progress,
};
beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));
it("registered event is accepted by the entry whitelist and only updates private progress", () => {
  expect(parseServerMessage(event)).not.toBeNull();
  useStructuredStore.getState().applyEvent(event as StructuredEventEnvelope);
  expect(useStructuredStore.getState().keeperProgress).toEqual(progress);
  expect(useStructuredStore.getState().clues).toEqual([]);
  expect(useStructuredStore.getState().items).toEqual([]);
});
it("refresh replaces progress; old or player snapshot and world switch remove all private progress", () => {
  const s = useStructuredStore.getState();
  s.applySnapshot({ keeper_progress: progress }, "world");
  expect(useStructuredStore.getState().keeperProgress).toEqual(progress);
  s.applySnapshot({}, "world");
  expect(useStructuredStore.getState().keeperProgress).toBeNull();
  s.applySnapshot({ keeper_progress: progress }, "world");
  s.bindWorld("another");
  expect(useStructuredStore.getState().keeperProgress).toBeNull();
});
it("malformed projection does not invent valid progress or reveal catalogue as player clues", () => {
  useStructuredStore.getState().applyEvent({
    ...event,
    payload: { ...progress, secret: "invalid" },
  } as StructuredEventEnvelope);
  expect(useStructuredStore.getState().keeperProgress).toBeNull();
});
it("plain grant remains knowledge only and rule index zero is retained as a number", () => {
  const spec = findKeeperCommand("grant_clue")!;
  const values = {
    ...emptyKeeperValues(spec),
    clue_id: "diary",
    recipient_investigator_ids: "alice",
    basis: "看过内容",
  };
  expect(buildKeeperPayload(spec, values)).toEqual({
    clue_id: "diary",
    recipient_investigator_ids: ["alice"],
    basis: "看过内容",
  });
  expect(
    buildKeeperPayload(spec, {
      ...values,
      discovery_rule_index: "0",
      discovery_investigator_id: "alice",
      acquire_item: true,
    }),
  ).toMatchObject({
    discovery_rule_index: 0,
    discovery_investigator_id: "alice",
    acquire_item: true,
  });
});
it("cannot select acquisition without a rule or forget to include the physical holder", () => {
  const spec = findKeeperCommand("grant_clue")!;
  const values = {
    ...emptyKeeperValues(spec),
    clue_id: "diary",
    recipient_investigator_ids: "bob",
    basis: "取得",
  };
  expect(
    validateKeeperFields(spec, { ...values, acquire_item: true }),
  ).toContain("请选择明确的模组发现规则，不能只勾选取得物品。");
  expect(
    validateKeeperFields(spec, {
      ...values,
      discovery_rule_index: "0",
      discovery_investigator_id: "alice",
    }),
  ).toContain("发现者必须包含在线索接收调查员中。");
});
it("use-effect requires declared ID/index/basis, ordinary use has no hidden effects", () => {
  const spec = findKeeperCommand("use_item")!;
  const values = {
    ...emptyKeeperValues(spec),
    investigator_id: "alice",
    item_id: "badge",
    quantity: "1",
    operation: "封印",
  };
  expect(validateKeeperFields(spec, values)).toEqual([]);
  expect(
    validateKeeperFields(spec, { ...values, effect_clue_id: "sealed" }),
  ).toContain("结算作者使用效果时，请选择效果、规则并填写适用依据。");
  expect(
    validateKeeperFields(spec, {
      ...values,
      effect_clue_id: "sealed",
      effect_rule_index: "0",
      basis: "实际覆在文档",
    }),
  ).toEqual([]);
});

it("known clue updates change original choices without duplicating the card, live and refresh agree", () => {
  const s = useStructuredStore.getState();
  s.applySnapshot(
    {
      clues: [
        {
          id: "seal",
          category: "investigation",
          text: "徽章",
          presentation: ["describe"],
        },
      ],
    },
    "world",
  );
  const acquired = {
    ...event,
    type: "clue_granted",
    payload: {
      clue_id: "seal",
      category: "investigation",
      text: "徽章",
      presentation: ["describe", "original"],
      allowed_physical_item_ids: ["badge"],
    },
  };
  s.applyEvent(acquired as StructuredEventEnvelope);
  expect(useStructuredStore.getState().clues).toHaveLength(1);
  expect(useStructuredStore.getState().clues[0].allowedPhysicalItemIds).toEqual(
    ["badge"],
  );
  s.applyEvent({
    ...acquired,
    event_id: 2,
    type: "clue_updated",
    payload: {
      ...acquired.payload,
      presentation: ["describe"],
      allowed_physical_item_ids: [],
    },
  } as StructuredEventEnvelope);
  expect(useStructuredStore.getState().clues).toHaveLength(1);
  expect(useStructuredStore.getState().clues[0].allowedPhysicalItemIds).toEqual(
    [],
  );
  const live = useStructuredStore.getState().clues;
  s.applySnapshot(
    {
      clues: [
        {
          id: "seal",
          category: "investigation",
          text: "徽章",
          presentation: ["describe"],
          allowed_physical_item_ids: [],
        },
      ],
    },
    "world",
  );
  expect(useStructuredStore.getState().clues).toEqual(live);
});

it("clue_updated cannot create previously unknown knowledge", () => {
  const s = useStructuredStore.getState();
  s.applyEvent({
    ...event,
    type: "clue_updated",
    payload: {
      clue_id: "secret",
      investigator_id: "alice",
      text: "不能凭更新获知新秘密",
      category: "investigation",
      presentation: ["describe"],
      allowed_physical_item_ids: [],
    },
  } as StructuredEventEnvelope);
  expect(useStructuredStore.getState().clues).toEqual([]);
});
