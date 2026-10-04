import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useAppStore } from "../../../state/app-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";
import { PanelActionDialog } from "./PanelActionDialog";

const send = vi.hoisted(() => vi.fn());
vi.mock("../../../options", () => ({ sendAction: send }));

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    mode: "local",
    activeWorldId: "legacy-keyboard",
    connection: "connected",
    inputEnabled: true,
    character: { inventory: [] },
  });
  useInvestigatorPanelStore.setState({ editor: null });
  useInvestigatorPanelStore.getState().openEditor({
    kind: "present",
    clueKey: "clue-1",
    clueSummary: "一封信",
    target: "医生",
    question: "",
    physicalItem: null,
  });
});

it("经典编辑器也排除隐藏和禁用控件，不抢其他浮层的 Escape", () => {
  render(<PanelActionDialog />);
  const dialog = screen.getByRole("dialog", { name: "出示线索" });
  const probe = document.createElement("div");
  probe.innerHTML =
    "<button>可见末尾</button><div hidden><input /></div><fieldset disabled><button>禁用末尾</button></fieldset>";
  dialog.append(probe);
  const last = probe.querySelector("button")!;
  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  expect(screen.getByRole("button", { name: "取消并关闭" })).toHaveFocus();
  const other = document.createElement("button");
  document.body.append(other);
  other.focus();
  fireEvent.keyDown(other, { key: "Escape" });
  expect(dialog.parentElement).not.toHaveClass("closing");
  expect(send).not.toHaveBeenCalled();
  other.remove();
  probe.remove();
});

it("经典编辑器关闭恢复触发者，不发行动", () => {
  const trigger = document.createElement("button");
  document.body.append(trigger);
  trigger.focus();
  const view = render(<PanelActionDialog />);
  expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(
    true,
  );
  act(() => useInvestigatorPanelStore.getState().closeEditor());
  expect(trigger).toHaveFocus();
  expect(send).not.toHaveBeenCalled();
  view.unmount();
  trigger.remove();
});

it("收起的窄屏抽屉不能承接关闭焦点，改回角色线索入口", () => {
  const drawer = document.createElement("aside");
  drawer.id = "char-panel";
  drawer.className = "collapsed";
  const trigger = document.createElement("button");
  drawer.append(trigger);
  const reopen = document.createElement("button");
  reopen.id = "btn-panel";
  document.body.append(drawer, reopen);
  trigger.focus();
  const view = render(<PanelActionDialog />);
  act(() => useInvestigatorPanelStore.getState().closeEditor());
  expect(reopen).toHaveFocus();
  expect(send).not.toHaveBeenCalled();
  view.unmount();
  drawer.remove();
  reopen.remove();
});
