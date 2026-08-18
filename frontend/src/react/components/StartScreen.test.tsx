import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { StartScreen } from "./StartScreen";

vi.mock("../../start", () => ({
  switchModule: vi.fn(),
  continueGame: vi.fn(),
  startGame: vi.fn(),
}));
vi.mock("../../settings", () => ({ openSettings: vi.fn() }));

describe("StartScreen", () => {
  beforeEach(() => {
    useStartStore.setState({
      gameStarted: false,
      gameStarting: false,
      view: "menu",
      modules: [{ id: "scarlet", title: "猩红文档" }],
      activeModule: "scarlet",
      activeModuleTitle: "猩红文档",
      moduleSwitchPending: false,
      charactersReady: true,
      characterGroups: [
        {
          id: "module",
          title: "模组调查员",
          characters: [
            {
              ref: { source: "module", id: "arthur" },
              id: "arthur",
              name: "阿瑟",
              occupation: "侦探",
              source_label: "猩红文档",
              hp: 10,
              max_hp: 10,
              san: 60,
              max_san: 60,
              reputation: 0,
              completed_modules: 0,
            },
          ],
        },
      ],
      selectedCharacterId: "arthur",
      selectedCharacterRef: { source: "module", id: "arthur" },
      hasSaves: false,
      hint: "",
    });
  });

  it("moves from module menu to investigator selection without DOM adapters", () => {
    render(<StartScreen />);
    const moduleSelect = screen.getByRole("button", { name: "当前模组" });
    const importButton = screen.getByRole("button", { name: /导入模组/ });
    // 自绘下拉触发器与导入按钮仍在同一行（.module-select-row）
    expect(moduleSelect.closest(".module-select-row")).toBe(
      importButton.parentElement,
    );
    fireEvent.click(screen.getByRole("button", { name: /开始新游戏/ }));
    // 双挂载：两个视图常驻，切换只翻转 view-off class
    expect(document.getElementById("character-select-view")).not.toHaveClass(
      "view-off",
    );
    expect(screen.getAllByText("阿瑟").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "以此调查员开始" }),
    ).toBeEnabled();
  });
});

describe("StartScreen 模组切换“翻页”动效", () => {
  class FakeImage {
    static instances: FakeImage[] = [];
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    src = "";
    constructor() {
      FakeImage.instances.push(this);
    }
  }

  const BG_VAR = "--module-bg-image";
  const SCARLET_BG = 'url("http://localhost/api/assets/scarlet/bg.png")';
  const MANSION_BG = 'url("http://localhost/api/assets/mansion/bg.png")';
  const CRIMSON_BG = 'url("http://localhost/api/assets/crimson/bg.png")';

  const overlay = () => document.getElementById("start-overlay")!;
  const box = () => document.getElementById("start-box")!;
  const layerImage = (selector: string) =>
    (document.querySelector(selector) as HTMLElement).style.getPropertyValue(
      "--layer-image",
    );
  const setModuleBg = (value: string) =>
    document.documentElement.style.setProperty(BG_VAR, value);

  function requestSwitch() {
    act(() => {
      useStartStore.setState({
        moduleSwitchPending: true,
        hint: "正在切换模组…",
      });
    });
  }

  function confirmModule(id: string, title: string) {
    // 服务端时序：theme 消息先更新背景变量，module_list 随后确认
    act(() => {
      useStartStore.setState({
        activeModule: id,
        activeModuleTitle: title,
        moduleSwitchPending: false,
      });
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    FakeImage.instances = [];
    vi.stubGlobal("Image", FakeImage);
    // zustand store 跨测试共享：上一用例确认过的模组必须重置，
    // 否则 confirmModule 落到相同 activeModule 不会触发预加载。
    useStartStore.setState({
      activeModule: "scarlet",
      activeModuleTitle: "猩红文档",
      moduleSwitchPending: false,
      view: "menu",
      modules: [
        { id: "scarlet", title: "猩红文档" },
        { id: "mansion", title: "疯狂宅邸" },
      ],
    });
    useAppStore.setState({
      title: "",
      subtitle: "",
      description: "",
      startButtonText: "",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.documentElement.style.removeProperty(BG_VAR);
  });

  it("首次挂载不播放切换动效", () => {
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);
    expect(overlay().className).toBe("");
    expect(document.querySelector(".start-bg-layer")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(overlay().className).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("flipping 期间冻结旧模组文案，翻页落定后才换装，避免可见瞬移", () => {
    useAppStore.setState({
      title: "猩红文档",
      subtitle: "旧副标题",
      description: "旧模组简介",
      startButtonText: "打开猩红文档",
    });
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);
    expect(document.getElementById("start-title")!.textContent).toBe(
      "猩红文档",
    );

    requestSwitch();
    expect(overlay()).toHaveClass("module-flipping");
    // 服务端 theme 消息先到：新模组文案已进入 store，
    // 但旧页面正在翻走，上面的内容必须仍是旧文案（像印在纸页上）。
    act(() => {
      useAppStore.setState({
        title: "疯狂宅邸",
        subtitle: "新副标题",
        description: "新模组简介",
        startButtonText: "点燃烛火，开始故事",
      });
      useStartStore.setState({ activeModuleTitle: "疯狂宅邸" });
    });
    expect(document.getElementById("start-title")!.textContent).toBe(
      "猩红文档",
    );
    expect(document.getElementById("start-description")!.textContent).toBe(
      "旧模组简介",
    );
    expect(
      screen.getByRole("button", { name: "打开猩红文档" }),
    ).toBeInTheDocument();
    // 下拉触发器也保持旧模组，不在可见状态换字
    expect(
      document.querySelector("#module-select .module-select-value")!
        .textContent,
    ).toBe("猩红文档");

    setModuleBg(MANSION_BG);
    confirmModule("mansion", "疯狂宅邸");
    act(() => {
      FakeImage.instances[0].onload?.();
    });
    // 新背景已垫入下层，但翻页未落定：仍在 flipping，内容仍是旧文案
    expect(overlay()).toHaveClass("module-flipping");
    expect(document.getElementById("start-title")!.textContent).toBe(
      "猩红文档",
    );

    act(() => {
      vi.advanceTimersByTime(520);
    });
    // 翻页落定 → entering：旧页已翻走，此刻换装用户不可见
    expect(overlay()).toHaveClass("module-entering");
    expect(document.getElementById("start-title")!.textContent).toBe(
      "疯狂宅邸",
    );
    expect(document.getElementById("start-description")!.textContent).toBe(
      "新模组简介",
    );
    expect(
      document.querySelector("#module-select .module-select-value")!
        .textContent,
    ).toBe("疯狂宅邸");

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(document.getElementById("start-title")!.textContent).toBe(
      "疯狂宅邸",
    );
  });

  it("flipping → 背景预加载垫入下层 → 翻页落定 entering → 无 class/timer/图层残留", () => {
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);

    requestSwitch();
    expect(overlay()).toHaveClass("module-flipping");
    // 旧页面整体翻走：翻页容器承载旧背景图层与内容盒
    expect(document.querySelector(".module-page-flip")).toBeInTheDocument();
    expect(
      document.querySelector(".module-page-flip #start-box"),
    ).toBeInTheDocument();
    expect(layerImage(".module-page-flip .start-bg-layer.outgoing")).toBe(
      SCARLET_BG,
    );
    expect(document.querySelector(".module-page-edge")).toBeInTheDocument();
    // 确认前下层不渲染新背景
    expect(document.querySelector(".start-bg-layer.incoming")).toBeNull();

    setModuleBg(MANSION_BG);
    confirmModule("mansion", "疯狂宅邸");
    // 新背景预加载完成前下层仍不渲染
    expect(FakeImage.instances).toHaveLength(1);
    expect(FakeImage.instances[0].src).toBe(
      "http://localhost/api/assets/mansion/bg.png",
    );
    expect(document.querySelector(".start-bg-layer.incoming")).toBeNull();

    act(() => {
      FakeImage.instances[0].onload?.();
    });
    // 预加载完成：新背景垫入下层，但翻页未落定，不进入 entering
    expect(layerImage(".start-bg-layer.incoming")).toBe(MANSION_BG);
    expect(overlay()).toHaveClass("module-flipping");
    expect(overlay()).not.toHaveClass("module-entering");

    act(() => {
      vi.advanceTimersByTime(520);
    });
    // 翻页落定：翻页容器与扫边移除，内容盒回到正常位置播 entering
    expect(overlay()).toHaveClass("module-entering");
    expect(box()).toHaveClass("module-entering");
    expect(document.querySelector(".module-page-flip")).toBeNull();
    expect(document.querySelector(".module-page-edge")).toBeNull();
    expect(layerImage(".start-bg-layer.incoming")).toBe(MANSION_BG);

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(overlay().className).toBe("");
    expect(box().className).toBe("");
    expect(document.querySelector(".start-bg-layer")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("网络慢于翻页：翻页先落定停在背景态，确认到达后再 entering", () => {
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);
    requestSwitch();
    expect(overlay()).toHaveClass("module-flipping");

    // 翻页动画结束时服务端尚未确认：页面翻走，停在纯背景态
    act(() => {
      vi.advanceTimersByTime(520);
    });
    expect(overlay()).toHaveClass("module-flipping");
    expect(overlay()).not.toHaveClass("module-entering");

    // 确认到达 + 预加载完成：立刻补 entering
    setModuleBg(MANSION_BG);
    confirmModule("mansion", "疯狂宅邸");
    act(() => {
      FakeImage.instances[0].onload?.();
    });
    expect(overlay()).toHaveClass("module-entering");
    expect(layerImage(".start-bg-layer.incoming")).toBe(MANSION_BG);

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(overlay().className).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("新背景加载失败时用新主题的静态回退背景，不闪白", () => {
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);
    requestSwitch();
    setModuleBg(MANSION_BG);
    confirmModule("mansion", "疯狂宅邸");

    act(() => {
      FakeImage.instances[0].onerror?.();
    });
    // 预加载失败即视为就绪（回退背景），翻页落定后进入 entering
    act(() => {
      vi.advanceTimersByTime(520);
    });
    expect(overlay()).toHaveClass("module-entering");
    // 回退到 --ui-start-bg 静态背景，而非透明或白屏
    expect(layerImage(".start-bg-layer.incoming")).toBe("var(--ui-start-bg)");

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(overlay().className).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("切换请求失败时恢复旧内容并取消动效", () => {
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);
    requestSwitch();
    expect(overlay()).toHaveClass("module-flipping");

    // 服务端 error → resetStartButton：pending 解除但模组不变
    act(() => {
      useStartStore.setState({
        moduleSwitchPending: false,
        hint: "模组不存在",
      });
    });
    expect(overlay().className).toBe("");
    expect(box().className).toBe("");
    expect(document.querySelector(".module-page-flip")).toBeNull();
    expect(document.querySelector(".start-bg-layer")).toBeNull();
    expect(document.querySelector(".module-page-edge")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("快速连续切换只保留最新目标，不排队播放旧动画", () => {
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);

    requestSwitch();
    setModuleBg(MANSION_BG);
    confirmModule("mansion", "疯狂宅邸");
    expect(FakeImage.instances).toHaveLength(1);

    // mansion 背景仍在预加载，用户又切向 crimson
    requestSwitch();
    expect(overlay()).toHaveClass("module-flipping");
    expect(overlay()).not.toHaveClass("module-entering");
    setModuleBg(CRIMSON_BG);
    confirmModule("crimson", "猩红文档");
    expect(FakeImage.instances).toHaveLength(2);

    // mansion 的预加载回调已被取消，迟到也不得改变当前过渡
    act(() => {
      FakeImage.instances[0].onload?.();
    });
    expect(overlay()).toHaveClass("module-flipping");
    expect(overlay()).not.toHaveClass("module-entering");

    act(() => {
      FakeImage.instances[1].onload?.();
    });
    expect(layerImage(".start-bg-layer.incoming")).toBe(CRIMSON_BG);
    act(() => {
      vi.advanceTimersByTime(520);
    });
    expect(overlay()).toHaveClass("module-entering");

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(overlay().className).toBe("");
    expect(document.querySelector(".start-bg-layer")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("prefers-reduced-motion 下不播放，确认后直接显示新模组", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("reduce"),
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
    setModuleBg(SCARLET_BG);
    render(<StartScreen />);

    requestSwitch();
    expect(overlay().className).toBe("");
    expect(document.querySelector(".start-bg-layer")).toBeNull();

    setModuleBg(MANSION_BG);
    confirmModule("mansion", "疯狂宅邸");
    expect(overlay().className).toBe("");
    expect(document.querySelector(".start-bg-layer")).toBeNull();
    expect(document.querySelector(".module-page-edge")).toBeNull();
    expect(FakeImage.instances).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("StartScreen 主菜单 ↔ 选择调查员 双挂载交叉过渡", () => {
  const menuView = () => document.getElementById("start-menu-view")!;
  const charactersView = () =>
    document.getElementById("character-select-view")!;

  function renderWithTwoCharacters(view: "menu" | "characters" = "menu") {
    useStartStore.setState({
      gameStarted: false,
      gameStarting: false,
      view,
      modules: [{ id: "scarlet", title: "猩红文档" }],
      activeModule: "scarlet",
      activeModuleTitle: "猩红文档",
      moduleSwitchPending: false,
      charactersReady: true,
      characterGroups: [
        {
          id: "module",
          title: "模组调查员",
          characters: [
            {
              ref: { source: "module", id: "arthur" },
              id: "arthur",
              name: "阿瑟",
              occupation: "侦探",
              source_label: "猩红文档",
              hp: 10,
              max_hp: 10,
              san: 60,
              max_san: 60,
              reputation: 0,
              completed_modules: 0,
            },
            {
              ref: { source: "module", id: "howard" },
              id: "howard",
              name: "霍华德",
              occupation: "医生",
              source_label: "猩红文档",
              hp: 12,
              max_hp: 12,
              san: 55,
              max_san: 55,
              reputation: 0,
              completed_modules: 0,
            },
          ],
        },
      ],
      selectedCharacterId: "arthur",
      selectedCharacterRef: { source: "module", id: "arthur" },
      hasSaves: false,
      hint: "",
    });
    return render(<StartScreen />);
  }

  it("初始：两页同栈常驻，仅当前页激活，非激活页 view-off + aria-hidden", () => {
    renderWithTwoCharacters();
    expect(menuView()).not.toHaveClass("view-off");
    expect(menuView().getAttribute("aria-hidden")).toBe("false");
    expect(charactersView()).toHaveClass("view-off");
    expect(charactersView().getAttribute("aria-hidden")).toBe("true");
  });

  it("进入角色选择：菜单退场（view-off），角色页激活，两页同时在场交叉", () => {
    renderWithTwoCharacters();
    fireEvent.click(screen.getByRole("button", { name: /开始新游戏/ }));

    // 交叉过渡不卸载旧页：两页都还在 DOM 中，只翻转状态
    expect(menuView()).toHaveClass("view-off");
    expect(menuView().getAttribute("aria-hidden")).toBe("true");
    expect(charactersView()).not.toHaveClass("view-off");
    expect(charactersView().getAttribute("aria-hidden")).toBe("false");
  });

  it("返回主菜单：状态即时反向翻转（CSS transition 自然反向重定向）", () => {
    renderWithTwoCharacters("characters");
    expect(menuView()).toHaveClass("view-off");

    fireEvent.click(screen.getByRole("button", { name: /返回主菜单/ }));
    expect(charactersView()).toHaveClass("view-off");
    expect(menuView()).not.toHaveClass("view-off");
  });

  it("切换调查员：档案卡重挂载播放入场，滚动位置复位", () => {
    renderWithTwoCharacters("characters");
    const detail = document.getElementById("character-detail")!;
    detail.scrollTop = 120;

    fireEvent.click(screen.getByRole("button", { name: /霍华德/ }));
    const dossier = document.querySelector(".character-dossier")!;
    expect(dossier).toHaveClass("dossier-anim");
    expect(dossier.textContent).toContain("霍华德");
    expect(detail.scrollTop).toBe(0);
  });

  it("换页不会重挂载档案卡（无双层动效：只有容器在动）", () => {
    renderWithTwoCharacters();
    const before = document.querySelector(".character-dossier");
    fireEvent.click(screen.getByRole("button", { name: /开始新游戏/ }));
    const after = document.querySelector(".character-dossier");
    // 同一 DOM 节点：交叉过渡只动容器，档案卡不重新入场
    expect(after).toBe(before);
  });
});

describe("StartScreen 开局进入游戏过渡", () => {
  const overlay = () => document.getElementById("start-overlay")!;

  beforeEach(() => {
    vi.useFakeTimers();
    useStartStore.setState({
      gameStarted: false,
      gameStarting: false,
      view: "characters",
      modules: [{ id: "scarlet", title: "猩红文档" }],
      activeModule: "scarlet",
      activeModuleTitle: "猩红文档",
      moduleSwitchPending: false,
      charactersReady: true,
      characterGroups: [],
      selectedCharacterId: "",
      selectedCharacterRef: null,
      hasSaves: false,
      hint: "",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("gameStarted 后整幕保持挂载播退出动画，结束后才隐藏", () => {
    render(<StartScreen />);
    expect(overlay()).not.toHaveClass("hidden");

    act(() => {
      useStartStore.setState({ gameStarted: true });
    });
    // 退出动画期间遮罩仍在场（淡出揭示游戏画面，无空帧），但不再可交互
    expect(overlay()).toHaveClass("start-closing");
    expect(overlay()).not.toHaveClass("hidden");
    expect(overlay().getAttribute("aria-hidden")).toBe("true");
    expect(document.getElementById("start-box")).not.toBeNull();

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(overlay()).toHaveClass("hidden");
    expect(overlay()).not.toHaveClass("start-closing");
    expect(document.getElementById("start-box")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("退出途中回到开局流程（gameStarted 回 false）取消退出，遮罩恢复展示", () => {
    render(<StartScreen />);
    act(() => {
      useStartStore.setState({ gameStarted: true });
    });
    expect(overlay()).toHaveClass("start-closing");

    act(() => {
      useStartStore.setState({ gameStarted: false, view: "menu" });
    });
    expect(overlay()).not.toHaveClass("start-closing");
    expect(overlay()).not.toHaveClass("hidden");
    expect(overlay().getAttribute("aria-hidden")).toBeNull();

    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(overlay()).not.toHaveClass("hidden");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("挂载时已 gameStarted（刷新恢复会话）不播动画，直接隐藏", () => {
    useStartStore.setState({ gameStarted: true });
    render(<StartScreen />);
    expect(overlay()).toHaveClass("hidden");
    expect(overlay()).not.toHaveClass("start-closing");
    expect(document.getElementById("start-box")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});
