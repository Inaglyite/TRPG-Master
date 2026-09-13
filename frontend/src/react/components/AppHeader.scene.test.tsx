import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { useSceneStore } from "../../state/scene-store";
import { useStartStore } from "../../state/start-store";
import { AppHeader } from "./AppHeader";

vi.mock("../../start", () => ({
  returnToStartMenu: vi.fn(),
}));

/** 开局后（服务端接受 start 并发出 gm_turn_start）才显示场景行。 */
function startGame() {
  act(() => {
    useStartStore.setState({ gameStarted: true, gameStarting: false });
  });
}

function sceneLine(container: HTMLElement) {
  return container.querySelector(".header-scene");
}

describe("AppHeader 当前场景行", () => {
  beforeEach(() => {
    useAppStore.setState({
      connection: "connected",
      title: "猩红文档",
      mode: "local",
    });
    useStartStore.setState({ gameStarted: false, gameStarting: false });
    useSceneStore.getState().reset();
  });

  it("开局前不显示场景行", () => {
    const { container } = render(<AppHeader />);
    expect(sceneLine(container)).toBeNull();
    expect(screen.queryByText(/当前场景/)).not.toBeInTheDocument();
  });

  it("开局后显示在模组标题下方，并标出当前已结算位置", () => {
    useSceneStore
      .getState()
      .applyScene("world-a", { name: "医学院地下停尸房" });
    const { container } = render(<AppHeader />);
    startGame();

    const line = sceneLine(container);
    expect(line).not.toBeNull();
    expect(line?.textContent).toBe("当前场景 · 医学院地下停尸房");
    // 标题在前、场景行紧随其后：位置就在模组标题下方。
    const heading = container.querySelector(".header-leading h1");
    expect(heading?.nextElementSibling).toBe(line);
  });

  it("同步期间与确实缺失分别显示占位文字", () => {
    const { container } = render(<AppHeader />);
    startGame();
    expect(container.querySelector(".header-scene-name")?.textContent).toBe(
      "正在同步位置…",
    );

    act(() => {
      useSceneStore.getState().applyScene("world-a", null);
    });
    expect(container.querySelector(".header-scene-name")?.textContent).toBe(
      "位置未知",
    );
  });

  it("地名切换只改这一行，不新增任何入口", () => {
    useSceneStore.getState().applyScene("world-a", { name: "莱特的办公室" });
    const { container } = render(<AppHeader />);
    startGame();
    expect(sceneLine(container)?.querySelector("button, a, img")).toBeNull();

    act(() => {
      useSceneStore.getState().applyScene("world-a", { name: "希布酒馆" });
    });
    expect(container.querySelector(".header-scene-name")?.textContent).toBe(
      "希布酒馆",
    );
  });

  it("超长地名仍可通过 title 查看完整名称", () => {
    const longName = "密斯卡托尼克大学历史系研究生自习室";
    useSceneStore.getState().applyScene("world-a", { name: longName });
    const { container } = render(<AppHeader />);
    startGame();

    const line = sceneLine(container);
    expect(line).toHaveAttribute("title", `当前场景 · ${longName}`);
    expect(line).toHaveAttribute("aria-label", `当前场景：${longName}`);
  });

  it("云端模式复用同一个顶栏组件显示队伍位置", () => {
    useAppStore.setState({ mode: "online" });
    useSceneStore.getState().setWorld("room-world");
    useSceneStore
      .getState()
      .applyScene("room-world", { name: "哈兰德·洛奇的历史系办公室" });
    const { container } = render(<AppHeader />);
    startGame();

    expect(sceneLine(container)?.textContent).toBe(
      "当前场景 · 哈兰德·洛奇的历史系办公室",
    );
  });
});
