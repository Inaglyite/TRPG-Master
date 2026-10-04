import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { loadNarrativeHistory } from "../../../api/structuredHistory";
import { useAppStore } from "../../../state/app-store";
import { useMessageStore } from "../../../state/message-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { InheritedHistoryControl } from "./InheritedHistoryControl";

vi.mock("../../../api/structuredHistory", async (original) => ({
  ...(await original<object>()),
  loadNarrativeHistory: vi.fn(),
}));
const read = vi.mocked(loadNarrativeHistory);
const record = {
  message_id: "archive:one",
  sequence: 2,
  text: "没有打开抽屉的旧申报",
  entry_kind: "action_request" as const,
  speaker: { kind: "investigator" as const, name: "爱丽丝" },
};
beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ mode: "online" });
  useMessageStore.setState({
    messages: [{ id: "live", kind: "gm", text: "当前时间线" }],
  });
  useStructuredStore.setState({
    ...initialStructuredState,
    identity: { ...initialStructuredState.identity, worldId: "branch" },
    inheritedHistory: { messages: [record], next_before_sequence: 2 },
  });
});

it("keeps ancestors collapsed and separate from live narration and outcomes", () => {
  render(<InheritedHistoryControl />);
  expect(screen.queryByText(record.text)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看分叉前历史" }));
  expect(screen.getByText(record.text)).toBeVisible();
  expect(screen.getByText("行动申报 · 不代表已执行")).toBeVisible();
  expect(screen.getByText("爱丽丝")).toBeVisible();
  expect(useMessageStore.getState().messages).toHaveLength(1);
  expect(read).not.toHaveBeenCalled();
});

it("labels a partial inherited archive rather than claiming a complete timeline", () => {
  useStructuredStore.setState({ inheritedHistoryIncomplete: true });
  render(<InheritedHistoryControl />);
  fireEvent.click(screen.getByRole("button", { name: "查看分叉前历史" }));
  expect(screen.getByText(/更早的旧分支未保存历史档案/)).toBeVisible();
  expect(screen.getByText(record.text)).toBeVisible();
});

it("reads only inherited scope and deduplicates the older page", async () => {
  read.mockResolvedValue({
    messages: [
      { ...record, message_id: "archive:old", sequence: 1, text: "更早内容" },
      record,
    ],
    next_before_sequence: null,
  });
  render(<InheritedHistoryControl />);
  fireEvent.click(screen.getByRole("button", { name: "查看分叉前历史" }));
  fireEvent.click(screen.getByRole("button", { name: "载入更早的档案" }));
  await screen.findByText("更早内容");
  expect(read).toHaveBeenCalledWith("branch", 2, false, "inherited");
  expect(screen.getAllByText(record.text)).toHaveLength(1);
  expect(useMessageStore.getState().messages).toHaveLength(1);
});

it("does not import delayed private history after an authoritative recovery", async () => {
  let finish!: (page: Awaited<ReturnType<typeof loadNarrativeHistory>>) => void;
  read.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<InheritedHistoryControl />);
  fireEvent.click(screen.getByRole("button", { name: "查看分叉前历史" }));
  fireEvent.click(screen.getByRole("button", { name: "载入更早的档案" }));
  act(() =>
    useStructuredStore.setState({
      inheritedHistory: { messages: [], next_before_sequence: null },
      historyGeneration: 1,
    }),
  );
  await act(async () =>
    finish({ messages: [record], next_before_sequence: null }),
  );
  expect(screen.queryByText(record.text)).not.toBeInTheDocument();
  expect(useStructuredStore.getState().inheritedHistory?.messages).toEqual([]);
});

it("offers retry without discarding the archive on network failure", async () => {
  read.mockRejectedValueOnce(new Error("连接中断"));
  read.mockResolvedValueOnce({ messages: [], next_before_sequence: null });
  render(<InheritedHistoryControl />);
  fireEvent.click(screen.getByRole("button", { name: "查看分叉前历史" }));
  fireEvent.click(screen.getByRole("button", { name: "载入更早的档案" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("连接中断");
  expect(screen.getByText(record.text)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "载入更早的档案" }));
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
});

it("clears ancestor data on world switch and describes old unsaved branches honestly", () => {
  render(<InheritedHistoryControl />);
  act(() => useStructuredStore.getState().bindWorld("new-world"));
  expect(useStructuredStore.getState().inheritedHistory).toBeNull();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  act(() => useStructuredStore.setState({ inheritedHistoryUnavailable: true }));
  expect(screen.getByText(/旧分支未保存/)).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
