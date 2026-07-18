import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useMessageStore } from "../../state/message-store";
import { MessageList } from "./MessageList";

describe("MessageList", () => {
  beforeEach(() => useMessageStore.setState({ messages: [], actionReset: 0 }));

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

  it("settles dice via the CSS fallback when WebGL is unavailable", () => {
    // jsdom 无 WebGL：isDice3DEligible 必然为 false，走经典 CSS 滚动路径
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
      expect(screen.getByText(/d100=32/)).toHaveClass("hidden");
      act(() => {
        vi.advanceTimersByTime(1200);
      });
      expect(screen.getByText("30")).toBeInTheDocument();
      expect(screen.getByText("2")).toBeInTheDocument();
      expect(screen.getByText(/d100=32/)).not.toHaveClass("hidden");
    } finally {
      vi.useRealTimers();
    }
  });
});
