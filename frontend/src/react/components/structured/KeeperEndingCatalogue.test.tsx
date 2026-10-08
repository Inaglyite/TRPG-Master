import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RulingState } from "../../../protocol/rulings";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { parseServerMessage } from "../../../protocol/server-message";
import { KeeperEndingCatalogue } from "./KeeperEndingCatalogue";

const state: RulingState = {
  flags: [],
  recent: [],
  eligible_endings: [],
  ending_catalog: [
    {
      id: "truth",
      title: "真相与封印",
      ending_type: "good",
      trigger: "案件收尾",
      description: "作者的封印结局。",
      eligible: false,
      can_prepare: false,
      blocked_reason: "结局前置条件尚未满足。",
      conditions: [
        {
          flag_id: "monster_defeated",
          expected_text: "true",
          current_text: "false",
          recorded: true,
          satisfied: false,
        },
        {
          flag_id: "secret_password",
          expected_text: '""',
          current_text: "",
          recorded: false,
          satisfied: false,
        },
      ],
    },
  ],
};

describe("主持结局核对页", () => {
  it("缺条件仍可阅读，未记录不同于false；阅读不准备或提交", () => {
    const prepare = vi.fn();
    render(
      <KeeperEndingCatalogue
        state={state}
        blocked={null}
        onPrepare={prepare}
      />,
    );
    expect(screen.getByRole("button", { name: "准备结算" })).toBeDisabled();
    expect(screen.getByText("条件未齐")).toBeVisible();
    fireEvent.click(screen.getByText("条件清单（2 项）"));
    expect(screen.getByText("false")).toBeVisible();
    expect(screen.getByText("未记录")).toBeVisible();
    expect(prepare).not.toHaveBeenCalled();
  });
  it("条件齐全但正在战斗时不能准备；解除条件后只回传结局ID", () => {
    const ready: RulingState = {
      ...state,
      ending_catalog: [
        {
          ...state.ending_catalog![0],
          eligible: true,
          can_prepare: true,
          blocked_reason: "",
        },
      ],
    };
    const prepare = vi.fn();
    const view = render(
      <KeeperEndingCatalogue
        state={ready}
        blocked="请先结束战斗。"
        onPrepare={prepare}
      />,
    );
    expect(screen.getByRole("button", { name: "准备结算" })).toBeDisabled();
    expect(screen.getByText("条件已齐")).toBeVisible();
    view.rerender(
      <KeeperEndingCatalogue
        state={ready}
        blocked={null}
        onPrepare={prepare}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "准备结算" }));
    expect(prepare).toHaveBeenCalledExactlyOnceWith("truth");
  });
  it("作者说明是文本，不执行HTML；旧服务端明确说明资料缺失", () => {
    const malicious: RulingState = {
      ...state,
      ending_catalog: [
        {
          ...state.ending_catalog![0],
          description: "<img src=x onerror=alert(1)>",
        },
      ],
    };
    const view = render(
      <KeeperEndingCatalogue
        state={malicious}
        blocked={null}
        onPrepare={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("作者说明"));
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(view.container.querySelector("img")).toBeNull();
    view.rerender(
      <KeeperEndingCatalogue
        state={{ flags: [], recent: [], eligible_endings: [] }}
        blocked={null}
        onPrepare={() => {}}
      />,
    );
    expect(screen.getByText(/完整条件尚未提供/)).toBeVisible();
    expect(screen.queryByText("条件清单（0 项）")).toBeNull();
  });
  it("正式事件入口可识别，实时投影与快照一致；坏刷新帧不抹旧资料", () => {
    useStructuredStore.setState({
      ...initialStructuredState,
      identity: {
        ...initialStructuredState.identity,
        worldId: "w",
        revision: 1,
      },
    });
    const event = {
      protocol_version: 1,
      world_id: "w",
      event_id: 2,
      sequence: 2,
      revision: 2,
      type: "ending_catalog_updated",
      cause_request_id: null,
      payload: state,
    };
    expect(parseServerMessage(event)).not.toBeNull();
    act(() => useStructuredStore.getState().applyEvent(event));
    expect(useStructuredStore.getState().keeperRulings).toEqual(state);
    act(() =>
      useStructuredStore.getState().applyEvent({
        ...event,
        event_id: 3,
        payload: { flags: [], recent: [], eligible_endings: [] },
      }),
    );
    expect(useStructuredStore.getState().keeperRulings).toEqual(state);
    act(() =>
      useStructuredStore
        .getState()
        .applySnapshot({ revision: 3, keeper_rulings: state }),
    );
    expect(useStructuredStore.getState().keeperRulings).toEqual(state);
    useStructuredStore.setState(initialStructuredState);
  });
});
