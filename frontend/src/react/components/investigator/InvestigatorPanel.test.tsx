import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../../state/app-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { sendAction } from "../../../options";
import { InvestigatorPanel } from "./InvestigatorPanel";

vi.mock("../../../options", () => ({
  sendAction: vi.fn(() => true),
}));

const sendActionMock = vi.mocked(sendAction);

function seedState() {
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    activeWorldId: "world-1",
    inputEnabled: true,
    dialog: null,
    ending: null,
    choices: [],
    character: {
      name: "黄千陆",
      occupation: "记者",
      hp: 8,
      max_hp: 10,
      san: 52,
      max_san: 60,
      attributes: { 侦查: 70, 聆听: 55 },
      inventory: ["手电筒", "绷带（2份）", "绷带（2份）"],
    },
    clues: {
      investigation: [
        { id: "c1", text: "莱特的日记\n记录了三名买家。" },
        { id: "c2", text: "第二段探案线索" },
      ],
      npc: [
        {
          id: "p1",
          text: "约翰·惠特克罗夫特医生",
          type: "profile",
          asset: {
            file: "doctor.png",
            asset_data_uri: "data:image/png;base64,AA==",
          },
        },
      ],
      custom_unknown_cat: [{ id: "u1", text: "未知分类线索" }],
    },
  });
  useOnlineStore.setState({ ...initialOnlineState });
  useInvestigatorPanelStore.setState({
    worldId: null,
    prefsByWorld: {},
    editor: null,
  });
}

describe("InvestigatorPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedState();
    render(<InvestigatorPanel />);
  });

  it("渲染三张独立卡片与权威数据", () => {
    expect(screen.getByRole("heading", { name: "黄千陆" })).toBeInTheDocument();
    expect(screen.getByText("8 / 10")).toBeInTheDocument();
    expect(screen.getByText("52 / 60")).toBeInTheDocument();
    // 三张卡片标题（前缀匹配卡片头，避免命中“探案线索”等分组）
    expect(
      screen.getByRole("button", { name: /^人物状态/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^线索/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^道具/ })).toBeInTheDocument();
    // 道具聚合：相同标签合并为一行 ×2
    expect(screen.getByText("手电筒")).toBeInTheDocument();
    expect(screen.getByText("×2")).toBeInTheDocument();
    // 线索计数含未知分类：2 探案 + 1 人物 + 1 其他 = 4
    expect(screen.getByText("共 4 条")).toBeInTheDocument();
  });

  it("三张卡片可分别折叠，折叠保留摘要", () => {
    const statusToggle = screen.getByRole("button", { name: /^人物状态/ });
    fireEvent.click(statusToggle);
    expect(statusToggle).toHaveAttribute("aria-expanded", "false");
    // 折叠后 body 常挂但进入 closed 态（0 高 + visibility 隐藏，做补间动画）
    expect(document.getElementById("inv-card-body-status")).toHaveClass(
      "closed",
    );
    // 折叠摘要仍显示 HP/SAN
    expect(screen.getByText(/HP 8\/10 · SAN 52\/60/)).toBeInTheDocument();
    // 线索卡不受影响
    expect(screen.getByText("莱特的日记")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^线索/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("线索筛选与分组：全部视图首组展开，未知分类进“其他”", () => {
    // 默认全部：首个非空分类（探案）展开，其余收起（常挂但 closed）
    expect(screen.getByText("莱特的日记")).toBeInTheDocument();
    const otherRow = () =>
      document.querySelector('[data-clue="custom_unknown_cat:u1"]');
    expect(otherRow()?.closest(".inv-clue-group-body")).toHaveClass("closed");
    // 展开“其他”组
    fireEvent.click(screen.getByRole("button", { name: /其他/ }));
    expect(otherRow()?.closest(".inv-clue-group-body")).not.toHaveClass(
      "closed",
    );
    // 切到“人物”筛选：只显示该分类
    fireEvent.click(screen.getByRole("tab", { name: "人物" }));
    expect(document.querySelector('[data-clue="npc:p1"]')).toBeInTheDocument();
    expect(screen.queryByText("莱特的日记")).not.toBeInTheDocument();
  });

  it("详情展开显示完整原文，并摘掉“新增”标记", () => {
    act(() => {
      // 新增一条线索触发“新增”标记
      useAppStore.getState().setClues({
        ...useAppStore.getState().clues,
        investigation: [
          ...(useAppStore.getState().clues.investigation || []),
          { id: "c3", text: "新拿到的口供\n口供的完整第二行内容" },
        ],
      });
    });
    expect(screen.getByText("新增")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "详情" })[2]);
    // 详情区展示完整原文（第二行只在详情里出现）
    expect(screen.getByText(/口供的完整第二行内容/)).toBeInTheDocument();
    expect(screen.queryByText("新增")).not.toBeInTheDocument();
  });

  it("线索图片预览可打开并用 Escape 关闭", () => {
    fireEvent.click(screen.getByRole("tab", { name: "人物" }));
    fireEvent.click(screen.getByRole("button", { name: "doctor.png" }));
    expect(screen.getAllByRole("img", { name: "doctor.png" })).toHaveLength(2);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getAllByRole("img", { name: "doctor.png" })).toHaveLength(1);
  });

  it("出示编辑器：空对象不可提交，取消不产生行动", async () => {
    fireEvent.click(screen.getAllByRole("button", { name: "出示" })[0]);
    const dialog = screen.getByRole("dialog", { name: "出示线索" });
    expect(dialog).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "确认出示" });
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    // 退场动画结束后才真正关闭
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(sendActionMock).not.toHaveBeenCalled();
  });

  it("出示编辑器：填写对象后提交一次说明类行动", async () => {
    fireEvent.click(screen.getAllByRole("button", { name: "出示" })[0]);
    fireEvent.change(screen.getByPlaceholderText("例如：惠特克罗夫特医生"), {
      target: { value: "惠特克罗夫特医生" },
    });
    const confirm = screen.getByRole("button", { name: "确认出示" });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(sendActionMock).toHaveBeenCalledTimes(1);
    expect(sendActionMock).toHaveBeenCalledWith(
      "我向惠特克罗夫特医生说明我已知的线索：「莱特的日记」，询问他对此的看法。",
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("出示编辑器：选择实物后预览变为出示持有物", () => {
    fireEvent.click(screen.getAllByRole("button", { name: "出示" })[0]);
    fireEvent.change(screen.getByPlaceholderText("例如：惠特克罗夫特医生"), {
      target: { value: "法伦" },
    });
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "手电筒" },
    });
    expect(screen.getByText(/不递交、不赠送、不消耗/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认出示" }));
    expect(sendActionMock).toHaveBeenCalledWith(
      "我向法伦出示随身携带的「手电筒」，让他查看，不递交、不赠送、不消耗。",
    );
  });

  it("使用编辑器：空用法不可提交，填写后提交尝试类行动", () => {
    fireEvent.click(screen.getAllByRole("button", { name: "使用" })[0]);
    const confirm = screen.getByRole("button", { name: "确认使用" });
    expect(confirm).toBeDisabled();
    fireEvent.change(
      screen.getByPlaceholderText("例如：照亮床底，检查是否有可见物品"),
      { target: { value: "照亮床底" } },
    );
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(sendActionMock).toHaveBeenCalledTimes(1);
    expect(sendActionMock).toHaveBeenCalledWith("我尝试用「手电筒」照亮床底。");
  });

  it("发送被拒时保留草稿并给出原因", () => {
    sendActionMock.mockReturnValueOnce(false);
    fireEvent.click(screen.getAllByRole("button", { name: "使用" })[0]);
    fireEvent.change(
      screen.getByPlaceholderText("例如：照亮床底，检查是否有可见物品"),
      { target: { value: "照亮床底" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "确认使用" }));
    expect(screen.getByRole("alert")).toHaveTextContent("稍候");
    // 对话框仍在，草稿未丢
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("输入未开放时确认按钮禁用并说明原因", () => {
    useAppStore.setState({ inputEnabled: false });
    fireEvent.click(screen.getAllByRole("button", { name: "使用" })[0]);
    fireEvent.change(
      screen.getByPlaceholderText("例如：照亮床底，检查是否有可见物品"),
      { target: { value: "照亮床底" } },
    );
    expect(screen.getByRole("button", { name: "确认使用" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("守秘人正在叙述");
  });

  it("空线索与空库存的占位文案", () => {
    act(() => {
      useAppStore.getState().setClues({});
      useAppStore.getState().setCharacter({ inventory: [] });
    });
    expect(screen.getByText("暂无记录")).toBeInTheDocument();
    expect(screen.getByText("暂无随身道具")).toBeInTheDocument();
  });

  it("公开 conditions 渲染本地化症状标签，缺失字段不显示", () => {
    expect(screen.queryByText("重伤")).not.toBeInTheDocument();
    act(() => {
      useAppStore.getState().setCharacter({
        ...useAppStore.getState().character,
        conditions: ["major_wound", "unconscious"],
      });
    });
    expect(screen.getByText("重伤")).toBeInTheDocument();
    expect(screen.getByText("昏迷")).toBeInTheDocument();
  });

  it("切换世界丢弃编辑器草稿", () => {
    fireEvent.click(screen.getAllByRole("button", { name: "使用" })[0]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    act(() => {
      useAppStore.getState().setWorld("world-2", "mansion");
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
