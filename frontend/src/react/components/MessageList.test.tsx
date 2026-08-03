import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { useMessageStore } from "../../state/message-store";
import { MessageList } from "./MessageList";

describe("MessageList", () => {
  beforeEach(() => {
    useMessageStore.setState({ messages: [], actionReset: 0 });
    useAppStore.setState({ mode: "local", character: null });
  });

  it("renders markdown while suppressing model-provided images", () => {
    render(<MessageList />);
    act(() => {
      useMessageStore.getState().replaceMessages([
        {
          id: "msg-1",
          kind: "gm",
          text: '**重要线索** ![不可信图片](https://example.test/x.png)<script>alert(1)</script><a href="javascript:alert(2)">危险链接</a>',
          turnId: "turn-1",
        },
      ]);
    });
    expect(screen.getByText("重要线索")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(document.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText("危险链接")).not.toHaveAttribute("href");
  });

  it("renders waiting and pending-roll states as React content", () => {
    render(<MessageList />);
    act(() => {
      useMessageStore.getState().replaceMessages([
        {
          id: "wait",
          kind: "loading",
          text: "守秘人正在处理",
          turnId: "turn-2",
        },
        {
          id: "roll-pending",
          kind: "roll-pending",
          text: "正在结算",
          turnId: "turn-2",
        },
      ]);
    });
    expect(screen.getByText("守秘人正在处理")).toBeInTheDocument();
    expect(screen.getByText("判定中")).toBeInTheDocument();
  });

  it("settles dice via the fallback path when WebGL is unavailable", () => {
    // jsdom 无 WebGL：isDice3DEligible 必然为 false，走回退路径直接落定
    vi.useFakeTimers();
    try {
      render(<MessageList />);
      act(() => {
        useMessageStore.getState().replaceMessages([
          {
            id: "dice-1",
            kind: "dice",
            text: "🎲 【侦查】d100=32 vs 70 → ◆ 困难成功",
            turnId: "turn-3",
            dice: [
              { min: 0, max: 9, final: 3, label: "十位", formatter: "tens" },
              { min: 0, max: 9, final: 2, label: "个位" },
            ],
          },
        ]);
      });
      expect(screen.getByText("命运之骰翻滚")).toBeInTheDocument();
      expect(screen.getByText(/d100=32/)).toHaveClass("hidden");
      act(() => {
        vi.advanceTimersByTime(1200);
      });
      expect(screen.getByText("命运之骰落定")).toBeInTheDocument();
      expect(screen.getByText(/d100=32/)).not.toHaveClass("hidden");
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders speech units with speaker name and keeper attribution", () => {
    render(<MessageList />);
    act(() => {
      useMessageStore.getState().replaceMessages([
        {
          id: "g1",
          kind: "gm",
          text: "合并文本",
          turnId: "t-seg",
          segments: [
            { kind: "narration", text: "雨还在下。" },
            {
              kind: "speech",
              text: "「莱特生前一直在隐瞒什么。」",
              speaker: { type: "npc", id: "bryce_fallon", name: "布莱斯·法伦" },
            },
          ],
        },
      ]);
    });

    expect(screen.getByText("守秘人")).toBeInTheDocument();
    expect(screen.getByText("布莱斯·法伦")).toBeInTheDocument();
    expect(screen.getByText(/莱特生前一直在隐瞒什么/)).toBeInTheDocument();
    expect(document.querySelectorAll(".chat-row")).toHaveLength(2);
    expect(document.querySelector(".keeper-bubble")).toBeInTheDocument();
    expect(document.querySelector(".npc-bubble")).toBeInTheDocument();
  });

  it("offers an accessible immediate reveal control while presenting", () => {
    render(<MessageList />);
    act(() => {
      useMessageStore.getState().replaceMessages([
        {
          id: "streaming-chat",
          kind: "gm",
          text: "雨声。",
          streaming: true,
          segments: [{ kind: "narration", text: "雨声。" }],
        },
      ]);
    });

    expect(
      screen.getByRole("button", { name: "显示全文" }),
    ).toBeInTheDocument();
  });

  it("renders plain gm messages without segments unchanged", () => {
    render(<MessageList />);
    act(() => {
      useMessageStore
        .getState()
        .replaceMessages([
          { id: "g2", kind: "gm", text: "纯文本叙述。", turnId: "t-plain" },
        ]);
    });

    expect(screen.getByText("守秘人")).toBeInTheDocument();
    expect(screen.getByText("纯文本叙述。")).toBeInTheDocument();
  });

  it("shows the investigator name on player messages", () => {
    useAppStore.getState().setCharacter({ name: "黄千陆" });
    render(<MessageList />);
    act(() => {
      useMessageStore
        .getState()
        .replaceMessages([
          { id: "p1", kind: "player", text: "我检查地毯。", turnId: "t-p" },
        ]);
    });

    expect(screen.getByText("黄千陆")).toBeInTheDocument();
  });

  it("多人玩家气泡使用消息权威 speaker，不借用查看者角色卡", () => {
    useAppStore.setState({
      mode: "online",
      character: { name: "当前查看者" },
    });
    render(<MessageList />);
    act(() => {
      useMessageStore.getState().replaceMessages([
        {
          id: "p-authoritative",
          kind: "player",
          text: "我去检查窗户。",
          turnId: "t-authoritative",
          speaker: {
            type: "investigator",
            id: "inv-remote",
            name: "远端调查员",
          },
        },
      ]);
    });

    expect(screen.getByText("远端调查员")).toBeInTheDocument();
    expect(screen.queryByText("当前查看者")).not.toBeInTheDocument();
  });

  it("多人旧历史缺少 actor 时显示通用调查员而非查看者姓名", () => {
    useAppStore.setState({
      mode: "online",
      character: { name: "不该显示的查看者" },
    });
    render(<MessageList />);
    act(() => {
      useMessageStore
        .getState()
        .replaceMessages([
          { id: "legacy-player", kind: "player", text: "旧行动" },
        ]);
    });

    expect(screen.getByText("调查员")).toBeInTheDocument();
    expect(screen.queryByText("不该显示的查看者")).not.toBeInTheDocument();
  });
});

describe("MessageList 联机回合操作按钮门禁", () => {
  beforeEach(async () => {
    const { initialOnlineState, useOnlineStore } =
      await import("../../state/online-store");
    useOnlineStore.setState({ ...initialOnlineState });
    useMessageStore.setState({ messages: [], actionReset: 0 });
    useAppStore.setState({ mode: "local", character: null });
  });

  async function setupRoom(role: "owner" | "player", mode: "online" | "local") {
    const { initialOnlineState, useOnlineStore } =
      await import("../../state/online-store");
    useOnlineStore.setState({
      ...initialOnlineState,
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      members: [{ user_id: "u1", username: "alice", role }],
    });
    useAppStore.setState({ mode });
    act(() => {
      useMessageStore.getState().replaceMessages([
        {
          id: "msg-1",
          kind: "gm",
          text: "守秘人的一段叙述",
          turnId: "turn-1",
          canRewrite: true,
          canBranch: true,
        },
      ]);
    });
  }

  const REWRITE_NAME = "重新叙述本回合，不改变判定结果";
  const BRANCH_NAME = "回到本次行动前并创建独立时间线分支";

  it("单机模式两个按钮都显示", async () => {
    await setupRoom("player", "local");
    render(<MessageList />);
    expect(
      screen.getByRole("button", { name: REWRITE_NAME }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: BRANCH_NAME }),
    ).toBeInTheDocument();
  });

  it("联机非房主：rewrite 与 branch 都隐藏", async () => {
    await setupRoom("player", "online");
    render(<MessageList />);
    expect(
      screen.queryByRole("button", { name: REWRITE_NAME }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: BRANCH_NAME }),
    ).not.toBeInTheDocument();
  });

  it("联机房主：显示 rewrite，branch 仍隐藏", async () => {
    await setupRoom("owner", "online");
    render(<MessageList />);
    expect(
      screen.getByRole("button", { name: REWRITE_NAME }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: BRANCH_NAME }),
    ).not.toBeInTheDocument();
  });
});
