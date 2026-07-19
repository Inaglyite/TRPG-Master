import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { useMessageStore } from "../../state/message-store";
import { MessageList } from "./MessageList";

describe("MessageList", () => {
  beforeEach(() => {
    useMessageStore.setState({ messages: [], actionReset: 0 });
    useAppStore.setState({ character: null });
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
});
