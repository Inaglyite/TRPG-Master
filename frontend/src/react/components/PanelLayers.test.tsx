import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import * as panels from "../../panels";
import { useAppStore, type AdventureEntry } from "../../state/app-store";
import { HandoutLayer, SavePanel } from "./PanelLayers";

/** 两个存档位（一次游戏 = 一棵时间线树）：疯狂宅邸进行中，猩红文档是另一局。 */
const twoAdventures: AdventureEntry[] = [
  {
    root_world_id: "root",
    module_name: "mansion_of_madness",
    module_title: "疯狂宅邸",
    slot_index: 1,
    turn_count: 4,
    created_at: "2026-08-11T02:00:00Z",
    active: true,
    character_name: "阿瑟",
    scene_name: "大厅",
    updated_at: "2026-08-12T02:00:00Z",
    timeline_count: 3,
    resume_world_id: "root",
    timelines: [
      {
        world_id: "root",
        label: "主时间线",
        is_branch: false,
        depth: 0,
        active: true,
        resumable: true,
        scene_name: "大厅",
        character_name: "阿瑟",
        save_count: 2,
        updated_at: "2026-08-12T02:00:00Z",
      },
      {
        world_id: "branch-a",
        label: "岔路",
        is_branch: true,
        parent_world_id: "root",
        depth: 1,
        resumable: true,
        scene_name: "书房",
        character_name: "阿瑟",
        save_count: 1,
        updated_at: "2026-08-11T02:00:00Z",
      },
      {
        world_id: "branch-b",
        label: "断档分支",
        is_branch: true,
        parent_world_id: "root",
        depth: 1,
        resumable: false,
        scene_name: "阁楼",
        character_name: "阿瑟",
        save_count: 0,
      },
    ],
  },
  {
    root_world_id: "root-2",
    module_name: "crimson_document",
    module_title: "猩红文档",
    slot_index: 2,
    turn_count: 1,
    created_at: "2026-08-12T01:00:00Z",
    active: false,
    character_name: "贝尔",
    scene_name: "序章",
    updated_at: "2026-08-10T02:00:00Z",
    timeline_count: 1,
    resume_world_id: "root-2",
    timelines: [
      {
        world_id: "root-2",
        label: "主时间线",
        is_branch: false,
        depth: 0,
        resumable: true,
        scene_name: "序章",
        character_name: "贝尔",
        save_count: 1,
      },
    ],
  },
];

// 面板内部换页是两阶段换场：点击后先播 120ms 旧页出场，再换到新页。
function openTimelinesView() {
  const card = document.querySelector('[data-adventure="root"]') as HTMLElement;
  fireEvent.click(within(card).getByRole("button", { name: "管理时间线" }));
  act(() => {
    vi.advanceTimersByTime(130);
  });
}

// 二级视图返回一级同样需要等旧页出场结束。
function backToAdventuresView() {
  fireEvent.click(screen.getByRole("button", { name: "← 存档列表" }));
  act(() => {
    vi.advanceTimersByTime(130);
  });
}

describe("React panel layers", () => {
  beforeEach(() => {
    useAppStore.setState({
      savePanelOpen: false,
      savePanelMode: "manage",
      mode: "local",
      renameSlotId: null,
      saves: [],
      worlds: [],
      adventures: [],
      adventuresReady: false,
      latestBranchTurnId: null,
      activeWorldId: "",
      handouts: [],
      clueToast: null,
    });
  });

  it("renders save metadata from application state", () => {
    useAppStore.setState({
      savePanelOpen: true,
      saves: [
        {
          id: "slot_001",
          label: "停尸间",
          character_name: "阿瑟",
          hp: 8,
          san: 52,
        },
      ],
    });
    render(<SavePanel />);
    expect(screen.getByText(/停尸间/)).toBeInTheDocument();
    expect(screen.getByText("阿瑟")).toBeInTheDocument();
    expect(screen.getByText("HP 8 SAN 52")).toBeInTheDocument();
  });

  it("renders text-labeled save action buttons without emoji or empty tooltips", () => {
    useAppStore.setState({
      savePanelOpen: true,
      saves: [
        { id: "slot_000", label: "自动存档" },
        { id: "slot_001", label: "停尸间" },
      ],
    });
    const { container } = render(<SavePanel />);
    // 文字标签按钮（emoji 在部分平台渲染为色块/豆腐块，已移除）
    expect(
      screen.getAllByRole("button", { name: "读取" }).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByRole("button", { name: "重命名" }).length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
    // 自动存档槽位不渲染删除按钮
    const autoRow = container.querySelector('[data-slot="slot_000"]');
    expect(autoRow?.querySelector(".save-action-del")).not.toBeInTheDocument();
    // 面板内所有带 data-tooltip 的按钮都必须非空，避免空黑框
    const tooltipButtons = container.querySelectorAll("button[data-tooltip]");
    for (const button of tooltipButtons) {
      expect(button.getAttribute("data-tooltip")?.trim()).not.toBe("");
    }
  });

  it("does not offer a switch into a timeline without a resume save", () => {
    useAppStore.setState({
      savePanelOpen: true,
      activeWorldId: "root",
      worlds: [
        {
          world_id: "root",
          label: "主时间线",
          active: true,
          is_branch: false,
        },
        {
          world_id: "broken-branch",
          label: "旧时间线",
          is_branch: true,
          resumable: false,
          parent_world_id: "root",
        },
      ],
    });
    render(<SavePanel />);
    const unavailable = screen.getByRole("button", {
      name: "该时间线没有可继续的存档",
    });
    expect(unavailable).toBeDisabled();
    expect(unavailable).toHaveTextContent("无存档");
  });

  it("only archives a non-current local branch after an explicit second confirmation", () => {
    const archiveWorld = vi.spyOn(panels, "archiveWorld");
    useAppStore.setState({
      savePanelOpen: true,
      activeWorldId: "root",
      worlds: [
        {
          world_id: "root",
          label: "主时间线",
          active: true,
          is_branch: false,
        },
        {
          world_id: "branch-a",
          label: "岔路",
          is_branch: true,
          resumable: true,
          parent_world_id: "root",
        },
        // 与当前树无关的世界没有删除入口（仅当前树的分支可归档）。
        {
          world_id: "unrelated-root",
          label: "另一主线",
          is_branch: false,
          resumable: true,
        },
      ],
    });
    render(<SavePanel />);

    // 当前时间线和根时间线都没有删除入口，只有离开的分支可以归档。
    expect(screen.getAllByRole("button", { name: "删除时间线" })).toHaveLength(
      1,
    );
    fireEvent.click(screen.getByRole("button", { name: "删除时间线" }));
    expect(archiveWorld).not.toHaveBeenCalled();
    expect(screen.getByText("删除此分支？")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(archiveWorld).toHaveBeenCalledWith("branch-a");
  });

  it("其他时间线的存档只能切换不能读取；时间线标签可定位选中存档", () => {
    const switchWorld = vi.spyOn(panels, "switchWorld");
    useAppStore.setState({
      savePanelOpen: true,
      activeWorldId: "root",
      saves: [
        {
          id: "slot_001",
          label: "停尸间",
          world_id: "root",
          timeline_label: "主时间线",
          world_active: true,
        },
        {
          id: "slot_000",
          label: "自动存档",
          world_id: "branch-a",
          timeline_label: "岔路",
          world_active: false,
        },
      ],
    });
    const { container } = render(<SavePanel />);

    // 属于其他时间线的存档：只有切换入口，没有读取/重命名/删除。
    const foreignRow = container.querySelector(
      '[data-slot="slot_000"]',
    ) as HTMLElement;
    expect(foreignRow).not.toBeNull();
    expect(
      within(foreignRow).getByRole("button", { name: "切换到该时间线" }),
    ).toBeInTheDocument();
    expect(
      within(foreignRow).queryByRole("button", { name: "读取" }),
    ).not.toBeInTheDocument();
    expect(
      within(foreignRow).queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();

    // 点击存档的时间线标签 → 选中该存档行。
    fireEvent.click(
      screen.getByRole("button", { name: "查看「岔路」的时间线" }),
    );
    expect(
      container.querySelector(".save-slot-entry.save-selected"),
    ).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "切换到该时间线" }));
    expect(switchWorld).toHaveBeenCalledWith("branch-a");
  });

  it("does not expose branch archival controls outside local mode", () => {
    useAppStore.setState({
      mode: "online",
      savePanelOpen: true,
      worlds: [
        {
          world_id: "branch-a",
          label: "岔路",
          is_branch: true,
          resumable: true,
        },
      ],
    });
    render(<SavePanel />);
    expect(
      screen.queryByRole("button", { name: "删除时间线" }),
    ).not.toBeInTheDocument();
  });

  it("gives rename confirm/cancel buttons non-empty tooltips", () => {
    useAppStore.setState({
      savePanelOpen: true,
      renameSlotId: "slot_001",
      saves: [{ id: "slot_001", label: "停尸间" }],
    });
    render(<SavePanel />);
    expect(screen.getByRole("button", { name: "确认重命名" })).toHaveAttribute(
      "data-tooltip",
      "确认重命名",
    );
    expect(screen.getByRole("button", { name: "取消重命名" })).toHaveAttribute(
      "data-tooltip",
      "取消重命名",
    );
  });

  it("renders handouts and clue feedback from application state", () => {
    useAppStore.setState({
      clueToast: "获得尸检线索",
      handouts: [
        {
          id: "court",
          file: "court.png",
          label: "考特",
          asset_data_uri: "data:image/png;base64,AA==",
          asset_url: "",
          entity_type: "npc",
          entity_id: "court",
        },
      ],
    });
    render(<HandoutLayer />);
    expect(screen.getByRole("img", { name: "考特" })).toBeInTheDocument();
    expect(screen.getByText("获得尸检线索")).toBeInTheDocument();
  });
});

describe("SavePanel 存档位 → 时间线两级视图", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.setState({
      savePanelOpen: true,
      savePanelMode: "manage",
      mode: "local",
      renameSlotId: null,
      saves: [],
      worlds: [],
      adventures: twoAdventures,
      adventuresReady: true,
      latestBranchTurnId: null,
      activeWorldId: "root",
      handouts: [],
      clueToast: null,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("一级视图展示存档位卡片：编号、模组名、当前标记、进度与保存时间", () => {
    render(<SavePanel />);
    expect(screen.getByTestId("save-panel-adventures")).toBeInTheDocument();

    const current = document.querySelector(
      '[data-adventure="root"]',
    ) as HTMLElement;
    expect(within(current).getByText("SAVE 01")).toBeInTheDocument();
    expect(within(current).getByText("疯狂宅邸")).toBeInTheDocument();
    expect(within(current).getByText("当前")).toBeInTheDocument();
    expect(
      within(current).getByText(/阿瑟 · 大厅 · 第 4 回合/),
    ).toBeInTheDocument();
    expect(current).toHaveTextContent(/最后保存/);

    const other = document.querySelector(
      '[data-adventure="root-2"]',
    ) as HTMLElement;
    expect(within(other).getByText("SAVE 02")).toBeInTheDocument();
    expect(within(other).getByText("猩红文档")).toBeInTheDocument();
    expect(within(other).queryByText("当前")).not.toBeInTheDocument();
  });

  it("存档卡片的“继续游戏”走 resumeAdventure", () => {
    const resumeAdventure = vi.spyOn(panels, "resumeAdventure");
    render(<SavePanel />);
    const current = document.querySelector(
      '[data-adventure="root"]',
    ) as HTMLElement;
    fireEvent.click(within(current).getByRole("button", { name: "继续游戏" }));
    expect(resumeAdventure).toHaveBeenCalledWith(
      expect.objectContaining({ root_world_id: "root" }),
    );
  });

  it("管理时间线进入二级视图：主/分支分组、当前标记、可返回", () => {
    render(<SavePanel />);
    openTimelinesView();

    expect(screen.getByTestId("save-panel-timelines")).toBeInTheDocument();
    const headings = Array.from(
      document.querySelectorAll(".timeline-section-heading"),
    ).map((node) => node.textContent);
    expect(headings).toEqual(["主时间线", "分支时间线"]);

    const rootRow = document.querySelector(
      '[data-world="root"]',
    ) as HTMLElement;
    expect(within(rootRow).getByText("当前")).toBeInTheDocument();
    expect(
      within(rootRow).getByRole("button", { name: "继续游戏" }),
    ).toBeInTheDocument();

    const brokenRow = document.querySelector(
      '[data-world="branch-b"]',
    ) as HTMLElement;
    const unavailable = within(brokenRow).getByRole("button", {
      name: "该时间线没有可继续的存档",
    });
    expect(unavailable).toBeDisabled();
    expect(unavailable).toHaveTextContent("无存档");

    backToAdventuresView();
    expect(screen.getByTestId("save-panel-adventures")).toBeInTheDocument();
  });

  it("非当前分支的“从此处继续”走 resumeTimeline 切换", () => {
    const resumeTimeline = vi.spyOn(panels, "resumeTimeline");
    render(<SavePanel />);
    openTimelinesView();

    const branchRow = document.querySelector(
      '[data-world="branch-a"]',
    ) as HTMLElement;
    fireEvent.click(
      within(branchRow).getByRole("button", { name: "从此处继续" }),
    );
    expect(resumeTimeline).toHaveBeenCalledWith("branch-a", false);
  });

  it("删除分支需二次确认；主时间线与当前时间线没有删除入口", () => {
    const archiveWorld = vi.spyOn(panels, "archiveWorld");
    render(<SavePanel />);
    openTimelinesView();

    const rootRow = document.querySelector(
      '[data-world="root"]',
    ) as HTMLElement;
    expect(
      within(rootRow).queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();

    const branchRow = document.querySelector(
      '[data-world="branch-a"]',
    ) as HTMLElement;
    fireEvent.click(within(branchRow).getByRole("button", { name: "删除" }));
    expect(archiveWorld).not.toHaveBeenCalled();
    expect(screen.getByText("删除此分支？")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    expect(archiveWorld).toHaveBeenCalledWith("branch-a");
  });

  it("重命名时间线调用 renameWorld", () => {
    const renameWorld = vi.spyOn(panels, "renameWorld");
    render(<SavePanel />);
    openTimelinesView();

    const branchRow = document.querySelector(
      '[data-world="branch-a"]',
    ) as HTMLElement;
    fireEvent.click(within(branchRow).getByRole("button", { name: "重命名" }));
    const input = within(branchRow).getByDisplayValue("岔路");
    fireEvent.change(input, { target: { value: "新岔路" } });
    fireEvent.click(
      within(branchRow).getByRole("button", { name: "确认重命名时间线" }),
    );
    expect(renameWorld).toHaveBeenCalledWith("branch-a", "新岔路");
  });

  it("联机模式整体回退平铺列表：不展示时间线两级视图（房间协议无分支）", () => {
    useAppStore.setState({
      mode: "online",
      saves: [{ id: "slot_000", label: "自动存档" }],
    });
    render(<SavePanel />);

    expect(
      screen.queryByTestId("save-panel-adventures"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "管理时间线" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "读取" })).toBeInTheDocument();
  });

  it("当前时间线展开存档点：自动存档带前缀且无删除按钮", () => {
    useAppStore.setState({
      saves: [
        {
          id: "slot_000",
          label: "",
          scene_name: "大厅",
          world_id: "root",
          created_at: "2026-08-12T02:00:00Z",
        },
        {
          id: "slot_001",
          label: "停尸间",
          world_id: "root",
          created_at: "2026-08-12T03:00:00Z",
        },
      ],
    });
    render(<SavePanel />);
    openTimelinesView();

    const rootRow = document.querySelector(
      '[data-world="root"]',
    ) as HTMLElement;
    const slots = rootRow.querySelector(".timeline-slots") as HTMLElement;
    expect(slots).not.toBeNull();
    expect(within(slots).getByText(/自动存档 · 大厅/)).toBeInTheDocument();

    const autoRow = slots.querySelector(
      '[data-slot="slot_000"]',
    ) as HTMLElement;
    expect(
      within(autoRow).queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();
    const manualRow = slots.querySelector(
      '[data-slot="slot_001"]',
    ) as HTMLElement;
    expect(
      within(manualRow).getByRole("button", { name: "删除" }),
    ).toBeInTheDocument();
  });

  it("load 模式提供读取/重命名/删除，并可查看存档位时间线", () => {
    useAppStore.setState({ savePanelMode: "load" });
    render(<SavePanel />);

    const current = document.querySelector(
      '[data-adventure="root"]',
    ) as HTMLElement;
    expect(
      within(current).getByRole("button", { name: "读取" }),
    ).toBeInTheDocument();
    // load 模式可查看时间线（只读浏览 + 从此处继续），管理措辞不出现。
    expect(
      within(current).getByRole("button", { name: "时间线" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "管理时间线" }),
    ).not.toBeInTheDocument();
    // 当前存档位可重命名但不可删除；其他存档位两者皆可。
    expect(
      within(current).getByRole("button", { name: "重命名" }),
    ).toBeInTheDocument();
    expect(
      within(current).queryByRole("button", { name: "删除存档" }),
    ).not.toBeInTheDocument();
    const other = document.querySelector(
      '[data-adventure="root-2"]',
    ) as HTMLElement;
    expect(
      within(other).getByRole("button", { name: "删除存档" }),
    ).toBeInTheDocument();
  });

  it("load 模式时间线视图：可继续/重命名/删除分支，但不展示存档点管理", () => {
    const resumeTimeline = vi.spyOn(panels, "resumeTimeline");
    useAppStore.setState({ savePanelMode: "load" });
    render(<SavePanel />);

    const current = document.querySelector(
      '[data-adventure="root"]',
    ) as HTMLElement;
    fireEvent.click(within(current).getByRole("button", { name: "时间线" }));
    act(() => {
      vi.advanceTimersByTime(130);
    });

    expect(screen.getByTestId("save-panel-timelines")).toBeInTheDocument();
    const branchRow = document.querySelector(
      '[data-world="branch-a"]',
    ) as HTMLElement;
    fireEvent.click(
      within(branchRow).getByRole("button", { name: "从此处继续" }),
    );
    expect(resumeTimeline).toHaveBeenCalledWith("branch-a", false);
    // load 模式同样提供时间线的重命名/删除（仅本地、非当前分支可删）。
    expect(
      within(branchRow).getByRole("button", { name: "重命名" }),
    ).toBeInTheDocument();
    expect(
      within(branchRow).getByRole("button", { name: "删除" }),
    ).toBeInTheDocument();
    const rootRow = document.querySelector(
      '[data-world="root"]',
    ) as HTMLElement;
    expect(
      within(rootRow).getByRole("button", { name: "重命名" }),
    ).toBeInTheDocument();
    expect(
      within(rootRow).queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();
    // 存档点管理（新建存档点/创建分支）仍只留在游戏内“存档管理”。
    expect(
      screen.queryByRole("button", { name: "新建存档点" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "从当前进度创建分支" }),
    ).not.toBeInTheDocument();
  });

  it("点击存档卡片信息区也会打开该存档位的时间线视图", () => {
    render(<SavePanel />);

    const current = document.querySelector(
      '[data-adventure="root"]',
    ) as HTMLElement;
    fireEvent.click(
      current.querySelector(".adventure-card-info") as HTMLElement,
    );
    act(() => {
      vi.advanceTimersByTime(130);
    });
    expect(screen.getByTestId("save-panel-timelines")).toBeInTheDocument();
  });

  it("重命名存档位调用 renameAdventure；自定义槽名替换模组名显示", () => {
    const renameAdventure = vi.spyOn(panels, "renameAdventure");
    render(<SavePanel />);

    const other = document.querySelector(
      '[data-adventure="root-2"]',
    ) as HTMLElement;
    fireEvent.click(within(other).getByRole("button", { name: "重命名" }));
    const input = within(other).getByPlaceholderText("猩红文档");
    fireEvent.change(input, { target: { value: "图书馆线" } });
    fireEvent.click(
      within(other).getByRole("button", { name: "确认重命名存档" }),
    );
    expect(renameAdventure).toHaveBeenCalledWith("root-2", "图书馆线");

    // 服务端推送后：槽名成为卡片标题，模组名退到次级信息行。
    act(() => {
      useAppStore.setState({
        adventures: twoAdventures.map((adventure) =>
          adventure.root_world_id === "root-2"
            ? { ...adventure, slot_name: "图书馆线" }
            : adventure,
        ),
      });
    });
    const renamed = document.querySelector(
      '[data-adventure="root-2"]',
    ) as HTMLElement;
    expect(within(renamed).getByText("图书馆线")).toBeInTheDocument();
    expect(renamed).toHaveTextContent(/猩红文档 · 最后保存/);
  });

  it("删除存档位需二次确认；当前存档位没有删除入口", () => {
    const archiveAdventure = vi.spyOn(panels, "archiveAdventure");
    render(<SavePanel />);

    const current = document.querySelector(
      '[data-adventure="root"]',
    ) as HTMLElement;
    expect(
      within(current).queryByRole("button", { name: "删除存档" }),
    ).not.toBeInTheDocument();

    const other = document.querySelector(
      '[data-adventure="root-2"]',
    ) as HTMLElement;
    fireEvent.click(within(other).getByRole("button", { name: "删除存档" }));
    expect(archiveAdventure).not.toHaveBeenCalled();
    expect(screen.getByText(/条时间线将一并归档/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(archiveAdventure).toHaveBeenCalledWith("root-2");
  });

  it("联机模式不提供删除/重命名存档位入口", () => {
    useAppStore.setState({ mode: "online" });
    render(<SavePanel />);
    expect(
      screen.queryByRole("button", { name: "删除存档" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重命名" }),
    ).not.toBeInTheDocument();
  });

  it("新服务端但没有任何存档位时展示空态而不是回退列表", () => {
    useAppStore.setState({ adventures: [], adventuresReady: true });
    render(<SavePanel />);
    expect(screen.getByTestId("save-panel-adventures")).toBeInTheDocument();
    expect(screen.getByText("还没有存档位")).toBeInTheDocument();
    expect(screen.getByText(/SAVE 01/)).toBeInTheDocument();
  });

  it("联机房间收到空 adventure_list（本地会话初始化序列复用）仍回退平铺存档列表", () => {
    useAppStore.setState({
      mode: "online",
      adventures: [],
      adventuresReady: true,
      saves: [{ id: "slot_000", label: "自动存档" }],
    });
    render(<SavePanel />);
    expect(
      screen.queryByTestId("save-panel-adventures"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "读取" })).toBeInTheDocument();
  });
});
