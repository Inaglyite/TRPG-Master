import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { NarrativeHistoryControl } from "./NarrativeHistoryControl";
import { loadNarrativeHistory } from "../../../api/structuredHistory";
import { restoreNarrativeHistory } from "../../../structured-history";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { useMessageStore } from "../../../state/message-store";
import { useAppStore } from "../../../state/app-store";
vi.mock("../../../api/structuredHistory", async (original) => ({
  ...(await original<object>()),
  loadNarrativeHistory: vi.fn(),
}));
const read = vi.mocked(loadNarrativeHistory);
const message = {
  message_id: "old",
  sequence: 1,
  text: "先前的公开叙事",
  speaker: { kind: "keeper" as const, name: "守秘人" },
};
const page = { messages: [message], next_before_sequence: null };
beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ mode: "online" });
  useMessageStore.setState({ messages: [] });
  useStructuredStore.setState({
    ...initialStructuredState,
    historyBeforeSequence: 5,
    identity: { ...initialStructuredState.identity, worldId: "one" },
  });
  read.mockResolvedValue(page);
});
it("loads older read-only text, preserving newer messages and deduplicating pages", async () => {
  restoreNarrativeHistory("one", {
    messages: [
      { ...message, message_id: "new", sequence: 6, text: "新的叙事" },
    ],
    next_before_sequence: 5,
  });
  render(<NarrativeHistoryControl />);
  fireEvent.click(screen.getByRole("button", { name: "载入更早叙事" }));
  await waitFor(() =>
    expect(useMessageStore.getState().messages.map((m) => m.text)).toEqual([
      "先前的公开叙事",
      "新的叙事",
    ]),
  );
  expect(read).toHaveBeenCalledWith("one", 5, false);
  expect(useStructuredStore.getState().historyBeforeSequence).toBeNull();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("does not duplicate an older message already present in the current page", async () => {
  restoreNarrativeHistory("one", page);
  const before = useMessageStore.getState().historyPrependRequest;
  render(<NarrativeHistoryControl />);
  fireEvent.click(screen.getByRole("button", { name: "载入更早叙事" }));
  await waitFor(() =>
    expect(useStructuredStore.getState().historyBeforeSequence).toBeNull(),
  );
  expect(
    useMessageStore.getState().messages.map((message) => message.text),
  ).toEqual(["先前的公开叙事"]);
  expect(useMessageStore.getState().historyPrependRequest).toBe(before);
});
it("does not allow double submission while reading", async () => {
  read.mockReturnValue(new Promise(() => {}));
  render(<NarrativeHistoryControl />);
  fireEvent.click(screen.getByRole("button"));
  expect(screen.getByRole("button")).toBeDisabled();
  expect(read).toHaveBeenCalledTimes(1);
});
it("keeps the cursor and permits retry after a read failure", async () => {
  read.mockRejectedValueOnce(new Error("会话已过期"));
  render(<NarrativeHistoryControl />);
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("会话已过期"),
  );
  expect(useStructuredStore.getState().historyBeforeSequence).toBe(5);
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() =>
    expect(useMessageStore.getState().messages).toHaveLength(1),
  );
});
it("ignores an old world's late response", async () => {
  let done!: (value: typeof page) => void;
  read.mockReturnValue(
    new Promise((resolve) => {
      done = resolve;
    }),
  );
  render(<NarrativeHistoryControl />);
  fireEvent.click(screen.getByRole("button"));
  act(() => useStructuredStore.getState().bindWorld("two"));
  await act(async () => {
    done(page);
    await Promise.resolve();
  });
  expect(useMessageStore.getState().messages).toEqual([]);
});
it("authoritative recovery replaces stale bubbles without creating dice or action buttons", () => {
  useMessageStore.setState({
    messages: [{ id: "stale", kind: "gm", text: "旧身份秘密" }],
  });
  restoreNarrativeHistory("one", page);
  expect(useMessageStore.getState().messages[0]).toMatchObject({
    text: "先前的公开叙事",
    streaming: false,
    canBranch: false,
    canRewrite: false,
  });
  expect(useMessageStore.getState().messages).toHaveLength(1);
});

it("ignores a late read after same-world recovery even with the same cursor", async () => {
  let done!: (value: typeof page) => void;
  read.mockReturnValue(
    new Promise((resolve) => {
      done = resolve;
    }),
  );
  render(<NarrativeHistoryControl />);
  fireEvent.click(screen.getByRole("button"));
  act(() => {
    useStructuredStore.getState().applySnapshot(
      {
        message_history: { messages: [], next_before_sequence: 5 },
      },
      "one",
    );
    restoreNarrativeHistory("one", { messages: [], next_before_sequence: 5 });
  });
  await act(async () => {
    done(page);
    await Promise.resolve();
  });
  expect(useMessageStore.getState().messages).toEqual([]);
  expect(useStructuredStore.getState().historyBeforeSequence).toBe(5);
  expect(screen.getByRole("button")).toBeEnabled();
});
