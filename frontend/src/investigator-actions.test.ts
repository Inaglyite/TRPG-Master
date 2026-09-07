import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildPanelActionText,
  buildPresentText,
  buildUseText,
  clueSummaryOf,
  panelActionBlockReason,
  staleDraftReason,
  submitPanelAction,
  validatePanelDraft,
  type PresentDraft,
  type UseDraft,
} from "./investigator-actions";
import { sendAction } from "./options";
import { useAppStore } from "./state/app-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";

vi.mock("./options", () => ({
  sendAction: vi.fn(),
}));

const sendActionMock = vi.mocked(sendAction);

function presentDraft(patch: Partial<PresentDraft> = {}): PresentDraft {
  return {
    kind: "present",
    clueKey: "investigation:clue-1",
    clueSummary: "莱特的日记",
    target: "惠特克罗夫特医生",
    question: "",
    physicalItem: null,
    ...patch,
  };
}

function useDraft(patch: Partial<UseDraft> = {}): UseDraft {
  return {
    kind: "use",
    itemLabel: "手电筒",
    usage: "照亮床底，检查是否有可见物品",
    target: "",
    ...patch,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  sendActionMock.mockReturnValue(true);
  useOnlineStore.setState({ ...initialOnlineState });
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    activeWorldId: "world-1",
    inputEnabled: true,
    dialog: null,
    ending: null,
    choices: [],
    character: { inventory: ["手电筒", "绷带（2份）"] },
    clues: {
      investigation: [{ id: "clue-1", text: "莱特的日记" }],
    },
  });
});

describe("线索摘要", () => {
  it("优先公共素材标签，否则取正文首行并截断", () => {
    expect(clueSummaryOf({ text: "正文", asset: { label: "日记封面" } })).toBe(
      "日记封面",
    );
    expect(clueSummaryOf({ text: "第一行\n第二行" })).toBe("第一行");
    expect(clueSummaryOf({ text: "长".repeat(60) })).toHaveLength(41);
    expect(clueSummaryOf({})).toBe("未命名线索");
  });
});

describe("行动文本构造", () => {
  it("说明线索：默认不创造实物、不转移", () => {
    expect(buildPresentText(presentDraft())).toBe(
      "我向惠特克罗夫特医生说明我已知的线索：「莱特的日记」，询问他对此的看法。",
    );
  });

  it("说明线索：带询问内容", () => {
    expect(
      buildPresentText(presentDraft({ question: "他是否见过这张便签" })),
    ).toBe(
      "我向惠特克罗夫特医生说明我已知的线索：「莱特的日记」，并询问：他是否见过这张便签。",
    );
  });

  it("出示实物：玩家明确选择库存物品时不递交、不消耗", () => {
    expect(buildPresentText(presentDraft({ physicalItem: "手电筒" }))).toBe(
      "我向惠特克罗夫特医生出示随身携带的「手电筒」，让他查看，不递交、不赠送、不消耗。",
    );
  });

  it("使用道具：只表达尝试意图", () => {
    expect(buildUseText(useDraft())).toBe(
      "我尝试用「手电筒」照亮床底，检查是否有可见物品。",
    );
    expect(buildUseText(useDraft({ usage: "包扎伤口", target: "自己" }))).toBe(
      "我尝试用「手电筒」对自己包扎伤口。",
    );
  });
});

describe("草稿校验", () => {
  it("出示必须有对象", () => {
    expect(validatePanelDraft(presentDraft({ target: " " }))).toContain("向谁");
    expect(validatePanelDraft(presentDraft())).toBeNull();
  });

  it("使用必须有用法", () => {
    expect(validatePanelDraft(useDraft({ usage: "" }))).toContain("怎么使用");
    expect(validatePanelDraft(useDraft())).toBeNull();
  });
});

describe("行动门禁", () => {
  it("本地模式连接正常且输入开放时放行", () => {
    expect(panelActionBlockReason()).toBeNull();
  });

  it("断线 / 叙述中 / 待决定 / 结局确认时拦截", () => {
    useAppStore.setState({ connection: "disconnected" });
    expect(panelActionBlockReason()).toContain("连接");
    useAppStore.setState({ connection: "connected", inputEnabled: false });
    expect(panelActionBlockReason()).toContain("叙述");
    useAppStore.setState({
      inputEnabled: true,
      dialog: { kind: "suggest", description: "x" },
    });
    expect(panelActionBlockReason()).toContain("检定");
    useAppStore.setState({
      dialog: null,
      ending: { ending_type: "good", title: "t", summary: "" },
    });
    expect(panelActionBlockReason()).toContain("结局");
    useAppStore.setState({
      ending: null,
      choices: [
        { label: "x", isFree: false, decisionId: "d", decisionOptionId: "o" },
      ],
    });
    expect(panelActionBlockReason()).toContain("决定");
  });

  it("在线模式非当前行动者拦截", () => {
    useAppStore.setState({ mode: "online", choices: [] });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      roomConnection: "connected",
      roomStatus: "playing",
      currentActorUserId: "u2",
      members: [{ user_id: "u1", username: "alice", role: "player" }],
    });
    expect(panelActionBlockReason()).toContain("轮到你");
  });
});

describe("过期草稿核对", () => {
  it("线索消失、实物消失、物品消失都会被拒绝", () => {
    const clues = { investigation: [{ id: "clue-1", text: "x" }] };
    expect(
      staleDraftReason(presentDraft(), "world-1", clues, ["手电筒"]),
    ).toBeNull();
    expect(staleDraftReason(presentDraft(), "world-1", {}, [])).toContain(
      "已不在",
    );
    expect(
      staleDraftReason(
        presentDraft({ physicalItem: "猎枪" }),
        "world-1",
        clues,
        ["手电筒"],
      ),
    ).toContain("猎枪");
    expect(
      staleDraftReason(useDraft({ itemLabel: "猎枪" }), "world-1", clues, [
        "手电筒",
      ]),
    ).toContain("猎枪");
  });
});

describe("提交", () => {
  it("成功时调用 sendAction 一次并返回 ok", () => {
    const result = submitPanelAction(presentDraft(), "world-1");
    expect(result.ok).toBe(true);
    expect(sendActionMock).toHaveBeenCalledTimes(1);
    expect(sendActionMock).toHaveBeenCalledWith(
      "我向惠特克罗夫特医生说明我已知的线索：「莱特的日记」，询问他对此的看法。",
    );
  });

  it("校验失败 / 世界切换 / 发送被拒时不发送", () => {
    expect(submitPanelAction(presentDraft({ target: "" }), "world-1").ok).toBe(
      false,
    );
    expect(submitPanelAction(presentDraft(), "world-2").ok).toBe(false);
    sendActionMock.mockReturnValue(false);
    const result = submitPanelAction(presentDraft(), "world-1");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("稍候");
    expect(sendActionMock).toHaveBeenCalledTimes(1);
  });
});
