import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { KeeperProgress } from "../../../protocol/keeper-progress";
import { KeeperProgressCatalogue } from "./KeeperProgressCatalogue";

const rule = {
  index: 0,
  intent: "search",
  skill: "spot_hidden",
  difficulty: "regular",
  requires_success: true,
  approach: "搜查暗格",
  sanity_note: "",
  conditions: [],
};
const clue = {
  id: "diary",
  category: "investigation",
  text: "未公开日记",
  discovered: false,
  granted_item: "私人日记",
  item_id: "",
  holder_id: "",
  related_scenes: ["study"],
  rules: [rule],
};
const state: KeeperProgress = {
  clues: [clue],
  clocks: [
    {
      id: "clue_clarity",
      title: "clue_clarity",
      value: 1,
      max: 5,
      level: "秘密进展",
      next_level: "未来秘密",
      advance_when: ["首次入册"],
    },
  ],
};
function show(data = state, blocked: string | null = null, sceneId = "study") {
  const prepare = vi.fn();
  render(
    <KeeperProgressCatalogue
      state={data}
      blocked={blocked}
      sceneId={sceneId}
      onPrepare={prepare}
    />,
  );
  fireEvent.click(screen.getByText(/模组线索目录/));
  fireEvent.click(screen.getByText(/私人日记 · 未记录/));
  return prepare;
}
it("author catalogue includes hidden entries without granting them and displays private clocks", () => {
  const prepare = show();
  expect(screen.getByText("未公开日记")).toBeVisible();
  expect(screen.getByText("秘密进展")).toBeVisible();
  expect(screen.getByText("案件时钟 · 仅主持可见")).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "1");
  expect(prepare).not.toHaveBeenCalled();
});
it("information, discovery and custody are three explicit preparation choices", () => {
  const p = show();
  fireEvent.click(screen.getByRole("button", { name: "准备发放信息" }));
  expect(p).toHaveBeenLastCalledWith("grant_clue", { clue_id: "diary" });
  fireEvent.click(screen.getByRole("button", { name: "准备结算发现" }));
  expect(p).toHaveBeenLastCalledWith("grant_clue", {
    clue_id: "diary",
    discovery_rule_index: "0",
  });
  fireEvent.click(screen.getByRole("button", { name: "准备取得实物" }));
  expect(p).toHaveBeenLastCalledWith("grant_clue", {
    clue_id: "diary",
    discovery_rule_index: "0",
    acquire_item: true,
  });
});
it("prepares exact authored check skill and object ID rather than scanning dialogue", () => {
  const p = show();
  fireEvent.click(screen.getByRole("button", { name: "准备发现检定" }));
  expect(p).toHaveBeenLastCalledWith(
    "request_check",
    expect.objectContaining({
      skill: "spot_hidden",
      target_kind: "scene_object",
      target_id: "diary",
    }),
  );
});
it("another scene prevents discovery/acquisition but does not erase keeper reference or sharing", () => {
  show(state, null, "library");
  expect(screen.getByRole("button", { name: "准备取得实物" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "准备发放信息" }),
  ).not.toBeDisabled();
});
it("declared unmet flags disable effect, while acquired objects never spawn a second copy", () => {
  show({
    ...state,
    clues: [
      {
        ...clue,
        item_id: "one-item",
        holder_id: "alice",
        rules: [
          {
            ...rule,
            conditions: [
              {
                flag_id: "open",
                expected_text: "true",
                current_text: "false",
                satisfied: false,
              },
            ],
          },
        ],
      },
    ],
  });
  expect(screen.getByRole("button", { name: "准备结算发现" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "准备取得实物" })).toBeDisabled();
});
it("use-type rules prepare real use_item instead of a generic narrative ruling", () => {
  const p = show({
    ...state,
    clues: [
      { ...clue, rules: [{ ...rule, intent: "use", requires_success: false }] },
    ],
  });
  expect(
    screen.queryByRole("button", { name: "准备结算发现" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "准备使用效果" }));
  expect(p).toHaveBeenLastCalledWith(
    "use_item",
    expect.objectContaining({
      effect_clue_id: "diary",
      effect_rule_index: "0",
    }),
  );
});
it("search and discovery filter do not mutate or publish any card", () => {
  const p = show();
  fireEvent.change(screen.getByLabelText("搜索线索"), {
    target: { value: "没有这个" },
  });
  expect(screen.getByText("显示 0 条")).toBeVisible();
  fireEvent.change(screen.getByLabelText("搜索线索"), {
    target: { value: "" },
  });
  fireEvent.change(screen.getByLabelText("查看范围"), {
    target: { value: "found" },
  });
  expect(screen.getByText("显示 0 条")).toBeVisible();
  expect(p).not.toHaveBeenCalled();
});
it("disconnected or unauthorized state disables all preparation buttons", () => {
  show(state, "连接不可用");
  for (const b of screen.getAllByRole("button")) expect(b).toBeDisabled();
});
