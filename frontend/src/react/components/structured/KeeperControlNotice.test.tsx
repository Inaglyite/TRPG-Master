import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import {
  EVENT_FIXTURES,
  STRUCTURED_CAPABILITIES_WIRE,
  WORLD_ID,
} from "../../../protocol/structured-fixtures";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import {
  sendKeeperCommand,
  setStructuredSender,
} from "../../../structured-transport";
import { KeeperControlNotice } from "./AssistedAgentCards";

let sent: Record<string, unknown>[];
beforeEach(() => {
  sent = [];
  setStructuredSender((frame) => {
    sent.push(frame as Record<string, unknown>);
    return true;
  });
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({
    mode: "online",
    connection: "connected",
    activeWorldId: WORLD_ID,
  });
  useOnlineStore.setState({
    user: { id: "me", username: "我" },
    members: [
      {
        user_id: "me",
        username: "我",
        role: "player",
        can_keeper: true,
        investigator: null,
      },
    ],
  });
});

function snapshot(mode: string, controller: string | null, worldId = WORLD_ID) {
  useStructuredStore.getState().applySnapshot(
    {
      ...EVENT_FIXTURES.snapshot.payload,
      keeper_mode: mode,
      keeper: controller ? { mode: "human", user_id: controller } : null,
      server_capabilities: {
        ...STRUCTURED_CAPABILITIES_WIRE,
        commands: [...STRUCTURED_CAPABILITIES_WIRE.commands, "control_keeper"],
      },
    },
    worldId,
  );
}

it("human keeper sees their actual control without being invited to take over themselves or return to AI", () => {
  snapshot("human", "me");
  render(<KeeperControlNotice />);
  expect(screen.getByText("你正在主持")).toBeInTheDocument();
  expect(
    screen.getByText(/本场由人类主持，不需要配置模型/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "接管主持" })).toBeNull();
  expect(screen.queryByRole("button", { name: "交还 AI 主持" })).toBeNull();
});

it("another human controller can be explicitly taken over, including in human-only mode", () => {
  snapshot("human", "someone-else");
  render(<KeeperControlNotice />);
  expect(screen.getByText("另一位人类主持持有控制权")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "接管主持" }));
  fireEvent.click(screen.getByRole("button", { name: "接管主持" }));
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({
    kind: "control_keeper",
    payload: { action: "take" },
  });
  expect(screen.queryByText(/交还 AI/)).toBeNull();
});

it("unrelated outcomes cannot finish a control submission; a matching rejection is actionable and unlocks it", () => {
  snapshot("agent", "other");
  render(<KeeperControlNotice />);
  fireEvent.click(screen.getByRole("button", { name: "接管主持" }));
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      event_id: 100,
      payload: { request_id: "unrelated", status: "completed" },
    }),
  );
  expect(screen.getByRole("button", { name: "接管主持" })).toBeDisabled();
  expect(screen.queryByText("服务端已确认主持操作。")).toBeNull();
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      type: "request_error",
      event_id: 101,
      payload: {
        request_id: sent[0].command_id,
        code: "revision_conflict",
        message: "世界已变化",
        retryable: true,
      },
    }),
  );
  expect(screen.getByText(/世界已变化/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "接管主持" })).toBeEnabled();
});

it("only a confirmed current human controller in AI mode can hand back control", () => {
  snapshot("agent", "other");
  const { rerender } = render(<KeeperControlNotice />);
  expect(screen.queryByRole("button", { name: "交还 AI 主持" })).toBeNull();
  act(() => snapshot("agent", "me"));
  rerender(<KeeperControlNotice />);
  fireEvent.click(screen.getByRole("button", { name: "交还 AI 主持" }));
  expect(sent[0]).toMatchObject({
    kind: "control_keeper",
    payload: { action: "release" },
  });
});

it("ordinary players have no control panel even when a host-control snapshot exists", () => {
  snapshot("agent", "other");
  useOnlineStore.setState({
    members: [
      {
        user_id: "me",
        username: "我",
        role: "owner",
        can_keeper: false,
        investigator: null,
      },
    ],
  });
  render(<KeeperControlNotice />);
  expect(screen.queryByTestId("keeper-control-notice")).toBeNull();
});

it("world switch discards old control feedback, even when the new world is also hostable", () => {
  snapshot("agent", "other");
  render(<KeeperControlNotice />);
  fireEvent.click(screen.getByRole("button", { name: "接管主持" }));
  expect(screen.getByText("操作已发送，等待收件确认。")).toBeInTheDocument();
  act(() => useStructuredStore.getState().bindWorld("different-world"));
  act(() => snapshot("human", "me", "different-world"));
  expect(screen.queryByText(/等待收件确认/)).toBeNull();
});

it("receipt is not completion; only the matching settled command produces success", () => {
  snapshot("human", null);
  render(<KeeperControlNotice />);
  fireEvent.click(screen.getByRole("button", { name: "接管主持" }));
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      type: "action_ack",
      event_id: 77,
      payload: { command_id: sent[0].command_id, status: "queued" },
    }),
  );
  expect(
    screen.getByText("服务端已收件，等待本次主持操作结算。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "接管主持" })).toBeDisabled();
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      event_id: 78,
      payload: { command_id: sent[0].command_id, status: "completed" },
    }),
  );
  expect(screen.getByText("服务端已确认主持操作。")).toBeInTheDocument();
});

it("disconnected control remains readable but cannot submit", () => {
  snapshot("human", null);
  useAppStore.setState({ connection: "disconnected" });
  render(<KeeperControlNotice />);
  expect(screen.getByRole("button", { name: "接管主持" })).toBeDisabled();
  expect(screen.getByText("连接恢复后才能提交主持操作。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "接管主持" }));
  expect(sent).toHaveLength(0);
});

it("retry explicitly discloses returning control to AI and continuing only the selected request", () => {
  snapshot("agent", "me");
  useStructuredStore.getState().registerOutgoing({
    requestId: "paused-action",
    kind: "move",
    label: "前往医学院",
    payload: {},
    digest: "fixture",
  });
  useStructuredStore.getState().applyEvent({
    ...EVENT_FIXTURES.actionCompleted,
    payload: {
      request_id: "paused-action",
      status: "paused",
      detail: "模型超时",
    },
  });
  render(<KeeperControlNotice />);
  expect(screen.getByText(/此前已结算的行动不会回滚/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /交还 AI 并重试/ }));
  expect(sent[0]).toMatchObject({
    kind: "control_keeper",
    payload: { action: "retry", request_id: "paused-action" },
  });
  expect(useStructuredStore.getState().requests["paused-action"].status).toBe(
    "paused",
  );
});

it("agentTakeover 标志不代替控制命令，没有命令时如实只读", () => {
  snapshot("agent", "other");
  useStructuredStore.getState().applyCapabilities({
    ...STRUCTURED_CAPABILITIES_WIRE,
    agent_takeover: true,
  });
  render(<KeeperControlNotice />);
  expect(screen.getByText(/当前状态仅供查看/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "接管主持" })).toBeNull();
  expect(sendKeeperCommand("control_keeper", { action: "take" }).ok).toBe(
    false,
  );
  expect(sent).toHaveLength(0);
});
