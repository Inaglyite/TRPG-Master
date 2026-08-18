import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  archiveSoloTimeline,
  fetchSoloTimelines,
  renameSoloTimeline,
  switchSoloTimeline,
} from "../../../api/worlds";
import { enterRoom, refreshWorlds } from "../../../online";
import { SoloTimelinePanel } from "./SoloTimelinePanel";

vi.mock("../../../api/worlds", () => ({
  archiveSoloTimeline: vi.fn(),
  fetchSoloTimelines: vi.fn(),
  renameSoloTimeline: vi.fn(),
  switchSoloTimeline: vi.fn(),
}));

vi.mock("../../../online", () => ({
  enterRoom: vi.fn(),
  errorMessage: (error: unknown, fallback: string) =>
    error instanceof Error ? error.message : fallback,
  refreshWorlds: vi.fn(),
}));

const world = {
  world_id: "w-root",
  module: "mod-1",
  role: "owner",
  resume_world_id: "w-main",
  metadata: { name: "雾中宅邸", play_mode: "solo", room_status: "playing" },
};

const timelinesPayload = {
  root_world_id: "w-root",
  active_world_id: "w-main",
  worlds: [
    {
      world_id: "w-main",
      label: "初入宅邸",
      is_branch: false,
      active: true,
      resumable: true,
      scene_name: "门厅",
      character_name: "黄千陆",
      updated_at: "2026-08-16T12:00:00Z",
    },
    {
      world_id: "w-branch",
      label: "另一条路",
      is_branch: true,
      parent_world_id: "w-main",
      active: false,
      resumable: true,
      scene_name: "书房",
      character_name: "黄千陆",
      updated_at: "2026-08-15T12:00:00Z",
    },
  ],
};

function renderPanel(onClose = vi.fn()) {
  render(
    <SoloTimelinePanel world={world} title="雾中宅邸" onClose={onClose} />,
  );
  return onClose;
}

function rowOf(worldId: string): HTMLElement {
  const row = document.querySelector(`[data-world="${worldId}"]`);
  if (!row) throw new Error(`找不到时间线行 ${worldId}`);
  return row as HTMLElement;
}

/** 等面板加载完成后再取行（时间线列表是异步拉取的）。 */
async function readyRow(worldId: string): Promise<HTMLElement> {
  await waitFor(() => rowOf(worldId));
  return rowOf(worldId);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchSoloTimelines).mockResolvedValue(timelinesPayload);
  vi.mocked(switchSoloTimeline).mockResolvedValue({
    root_world_id: "w-root",
    active_world_id: "w-branch",
  });
  vi.mocked(renameSoloTimeline).mockResolvedValue({
    world_id: "w-branch",
    label: "新名字",
  });
  vi.mocked(archiveSoloTimeline).mockResolvedValue({
    world_id: "w-branch",
    fallback_world_id: "w-main",
  });
});

describe("SoloTimelinePanel 渲染", () => {
  it("加载后分“主时间线/分支时间线”两段，并标出当前 badge", async () => {
    renderPanel();
    expect(screen.getByRole("status")).toHaveTextContent("正在读取时间线");
    expect(await screen.findByText("主时间线")).toBeInTheDocument();
    expect(screen.getByText("分支时间线")).toBeInTheDocument();
    expect(screen.getByText("初入宅邸")).toBeInTheDocument();
    expect(screen.getByText("另一条路")).toBeInTheDocument();
    expect(within(rowOf("w-main")).getByText("当前")).toBeInTheDocument();
    expect(fetchSoloTimelines).toHaveBeenCalledWith("w-root");
  });

  it("meta 行展示场景、调查员与相对时间", async () => {
    renderPanel();
    await screen.findByText("初入宅邸");
    expect(
      within(rowOf("w-main")).getByText(/门厅 · 黄千陆 · /),
    ).toBeInTheDocument();
  });

  it("当前时间线与主时间线不显示删除按钮", async () => {
    renderPanel();
    await screen.findByText("初入宅邸");
    expect(
      within(rowOf("w-main")).queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();
    expect(
      within(rowOf("w-branch")).getByRole("button", { name: "删除" }),
    ).toBeInTheDocument();
  });

  it("读取失败显示可读错误并可重试", async () => {
    vi.mocked(fetchSoloTimelines)
      .mockRejectedValueOnce(new Error("网络错误"))
      .mockResolvedValueOnce(timelinesPayload);
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("网络错误");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("初入宅邸")).toBeInTheDocument();
    expect(fetchSoloTimelines).toHaveBeenCalledTimes(2);
  });

  it("关闭按钮与遮罩点击都会关闭面板", async () => {
    const onClose = renderPanel();
    await screen.findByText("初入宅邸");
    fireEvent.click(screen.getByRole("button", { name: "关闭时间线面板" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(document.querySelector(".solo-timeline-overlay")!);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe("SoloTimelinePanel 继续游戏", () => {
  it("当前时间线：直接按大厅 resume_world_id 进房，不调用 switch", async () => {
    renderPanel();
    const row = within(await readyRow("w-main"));
    fireEvent.click(await row.findByRole("button", { name: "继续游戏" }));
    await waitFor(() => expect(enterRoom).toHaveBeenCalledWith("w-main"));
    expect(switchSoloTimeline).not.toHaveBeenCalled();
  });

  it("非当前时间线：先 switch 成功再按目标时间线进房", async () => {
    renderPanel();
    const row = within(await readyRow("w-branch"));
    fireEvent.click(await row.findByRole("button", { name: "继续游戏" }));
    await waitFor(() => expect(enterRoom).toHaveBeenCalledWith("w-branch"));
    expect(switchSoloTimeline).toHaveBeenCalledWith("w-root", "w-branch");
  });

  it("switch 失败时在面板内联报错且不进房", async () => {
    vi.mocked(switchSoloTimeline).mockRejectedValue(
      new Error("当前有正在进行的回合"),
    );
    renderPanel();
    const row = within(await readyRow("w-branch"));
    fireEvent.click(await row.findByRole("button", { name: "继续游戏" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "当前有正在进行的回合",
    );
    expect(enterRoom).not.toHaveBeenCalled();
  });
});

describe("SoloTimelinePanel 重命名", () => {
  it("行内输入新名并确认后调用 rename 并刷新列表", async () => {
    renderPanel();
    const row = within(await readyRow("w-branch"));
    fireEvent.click(await row.findByRole("button", { name: "重命名" }));
    const input = screen.getByLabelText("时间线名称");
    fireEvent.change(input, { target: { value: "新名字" } });
    fireEvent.click(screen.getByRole("button", { name: "确认重命名时间线" }));
    await waitFor(() =>
      expect(renameSoloTimeline).toHaveBeenCalledWith(
        "w-root",
        "w-branch",
        "新名字",
      ),
    );
    // mutation 成功后重新拉取面板数据并刷新大厅列表
    await waitFor(() => expect(fetchSoloTimelines).toHaveBeenCalledTimes(2));
    expect(refreshWorlds).toHaveBeenCalled();
  });

  it("取消重命名不调用接口", async () => {
    renderPanel();
    const row = within(await readyRow("w-branch"));
    fireEvent.click(await row.findByRole("button", { name: "重命名" }));
    fireEvent.click(screen.getByRole("button", { name: "取消重命名时间线" }));
    expect(renameSoloTimeline).not.toHaveBeenCalled();
  });
});

describe("SoloTimelinePanel 删除分支", () => {
  it("需要行内二次确认；确认后 archive 并刷新列表", async () => {
    renderPanel();
    const row = within(await readyRow("w-branch"));
    fireEvent.click(await row.findByRole("button", { name: "删除" }));
    expect(archiveSoloTimeline).not.toHaveBeenCalled();
    expect(screen.getByText("删除此分支？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() =>
      expect(archiveSoloTimeline).toHaveBeenCalledWith("w-root", "w-branch"),
    );
    await waitFor(() => expect(fetchSoloTimelines).toHaveBeenCalledTimes(2));
    expect(refreshWorlds).toHaveBeenCalled();
  });

  it("取消二次确认不删除", async () => {
    renderPanel();
    const row = within(await readyRow("w-branch"));
    fireEvent.click(await row.findByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(archiveSoloTimeline).not.toHaveBeenCalled();
    expect(
      within(rowOf("w-branch")).getByRole("button", { name: "删除" }),
    ).toBeInTheDocument();
  });

  it("archive 失败时内联报错（如当前时间线不可删除）", async () => {
    vi.mocked(archiveSoloTimeline).mockRejectedValue(
      new Error("不能删除当前正在使用的时间线"),
    );
    renderPanel();
    const row = within(await readyRow("w-branch"));
    fireEvent.click(await row.findByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "不能删除当前正在使用的时间线",
    );
  });
});
