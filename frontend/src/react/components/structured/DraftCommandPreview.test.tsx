import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import { useStructuredStore } from "../../../state/structured-store";
import { DraftCommandPreview } from "./DraftCommandPreview";

beforeEach(() => {
  useStructuredStore.getState().reset();
  useStructuredStore.setState({
    targets: [
      { kind: "investigator", id: "inv-alice", name: "爱丽丝" },
      { kind: "npc", id: "guard", name: "守卫" },
    ],
  });
});

it("时间草稿活动类型为中文，省略时说明默认等待，不推断原因", () => {
  const command = {
    kind: "advance_time",
    payload: {
      minutes: 20,
      activity: "travel",
      reason: "wait（说明文字）",
    },
  };
  const before = JSON.stringify(command);
  render(
    <ol>
      <DraftCommandPreview command={command} index={0} />
    </ol>,
  );
  expect(screen.getByText("活动类型")).toBeVisible();
  expect(screen.getByText("赶路（仅计时）")).toBeVisible();
  expect(screen.getByText(/未指定活动类型时按等待计时/)).toBeVisible();
  expect(screen.getByText(command.payload.reason)).toBeVisible();
  expect(JSON.stringify(command)).toBe(before);
});

it("用可读标签和权威名称审阅，原参数默认折叠且不改载荷", () => {
  const command = {
    kind: "combat_action",
    payload: {
      actor_id: "inv-alice",
      target_id: "guard",
      action_type: "firearm",
      damage_spec: "1d3",
    },
  };
  const before = JSON.stringify(command);
  render(
    <ol>
      <DraftCommandPreview command={command} index={0} />
    </ol>,
  );
  expect(screen.getByText("批准战斗动作")).toBeVisible();
  expect(screen.getByText("爱丽丝")).toBeVisible();
  expect(screen.getByText("守卫")).toBeVisible();
  expect(screen.getByText("射击")).toBeVisible();
  const summary = screen.getByText("查看原始参数");
  const raw = summary.parentElement!.querySelector("pre")!;
  expect(raw).not.toBeVisible();
  fireEvent.click(summary);
  expect(JSON.parse(raw.textContent!)).toEqual(command);
  expect(JSON.stringify(command)).toBe(before);
});

it("不丢弃零值/否值，未识别字段保持可核对", () => {
  render(
    <ol>
      <DraftCommandPreview
        command={{
          kind: "record_ruling",
          payload: {
            value: false,
            expected_before: 0,
            basis: "待核对",
            extra: "不能静默隐藏",
          },
        }}
        index={0}
      />
    </ol>,
  );
  expect(screen.getByText("否")).toBeVisible();
  expect(screen.getByText("0")).toBeVisible();
  expect(screen.getByText("参数：extra")).toBeVisible();
  expect(screen.getByText("不能静默隐藏")).toBeVisible();
});

it("同名武器保留选定编号，不把审核建议折叠成模糊名称", () => {
  useStructuredStore.setState({
    items: [
      { id: "weapon-first", label: "手枪（3发）", quantity: 1, operations: [] },
      {
        id: "weapon-second",
        label: "手枪（3发）",
        quantity: 1,
        operations: [],
      },
    ],
  });
  const command = {
    kind: "combat_action",
    payload: {
      actor_id: "inv-alice",
      action_type: "firearm",
      weapon_item_id: "weapon-second",
    },
  };
  render(
    <ol>
      <DraftCommandPreview command={command} index={0} />
    </ol>,
  );
  expect(screen.getByText("手枪（3发） · weapon-second")).toBeVisible();
  expect(screen.queryByText("手枪（3发） · weapon-first")).toBeNull();
  expect(command.payload.weapon_item_id).toBe("weapon-second");
});

it("叙事与对象名称只作文本，不执行HTML", () => {
  const text = '<img src=x onerror="alert(1)">';
  const { container } = render(
    <ol>
      <DraftCommandPreview
        command={{
          kind: "publish_message",
          payload: {
            text,
            speaker: { kind: "keeper" },
            audience: { kind: "public" },
          },
        }}
        index={0}
      />
    </ol>,
  );
  expect(screen.getByText(text)).toBeVisible();
  expect(screen.getByText("守秘人")).toBeVisible();
  expect(screen.getByText("所有人")).toBeVisible();
  expect(container.querySelector("img")).toBeNull();
});

it("同名编号在不同命名空间里不串成别的对象", () => {
  useStructuredStore.setState({
    targets: [{ kind: "npc", id: "shared-id", name: "同编号的医生" }],
    destinations: [{ id: "shared-id", name: "同编号的医学院" }],
  });
  render(
    <ol>
      <DraftCommandPreview
        command={{
          kind: "combat_action",
          payload: {
            target_id: "shared-id",
            action_type: "melee",
            actor_id: "inv-alice",
          },
        }}
        index={0}
      />
      <DraftCommandPreview
        command={{
          kind: "move_party",
          payload: { destination_scene_id: "shared-id" },
        }}
        index={1}
      />
    </ol>,
  );
  expect(screen.getByText("同编号的医生")).toBeVisible();
  expect(screen.getByText("同编号的医学院")).toBeVisible();
});
