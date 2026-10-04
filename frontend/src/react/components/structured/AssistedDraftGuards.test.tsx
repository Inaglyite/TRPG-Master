import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import {
  EVENT_FIXTURES,
  STRUCTURED_CAPABILITIES_WIRE,
  WORLD_ID,
} from "../../../protocol/structured-fixtures";
import { useAppStore } from "../../../state/app-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import {
  resetStructuredTransport,
  sendKeeperCommand,
  resendStructuredRequest,
  setStructuredSender,
} from "../../../structured-transport";
import { AssistedDraftCard } from "./AssistedAgentCards";

let sent: Record<string, unknown>[];
const draft = {
  draftId: "private-draft",
  summary: "主持秘密：墙后藏着线索",
  narration: "建议打开夹层。",
  command: { kind: "advance_time", payload: { minutes: 10, reason: "调查" } },
  note: "",
  createdAt: 1,
};
beforeEach(() => {
  resetStructuredTransport();
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
    ...initialOnlineState,
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
  useStructuredStore.getState().applySnapshot(
    {
      ...EVENT_FIXTURES.snapshot.payload,
      keeper_mode: "assisted",
      server_capabilities: {
        ...STRUCTURED_CAPABILITIES_WIRE,
        commands: [...STRUCTURED_CAPABILITIES_WIRE.commands, "resolve_draft"],
      },
    },
    WORLD_ID,
  );
  useStructuredStore.setState({ keeperDraft: draft });
});
afterEach(() => resetStructuredTransport());

it("授权撤销后立即隐藏草稿；房主身份不能保留秘密或批准入口", () => {
  render(<AssistedDraftCard />);
  expect(screen.getByText(draft.summary)).toBeVisible();
  expect(screen.getByText("主持草稿（仅主持可见）")).toBeVisible();
  act(() =>
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
    }),
  );
  expect(screen.queryByTestId("keeper-draft-card")).toBeNull();
  expect(
    sendKeeperCommand("resolve_draft", {
      draft_id: draft.draftId,
      decision: "approved",
    }).ok,
  ).toBe(false);
  expect(sent).toHaveLength(0);
});

it("恢复同步期间保留草稿可读，但不批准、不拒绝、不产生请求", () => {
  useAppStore.setState({ connection: "connecting" });
  render(<AssistedDraftCard />);
  expect(screen.getByText(draft.summary)).toBeVisible();
  expect(screen.getByTestId("draft-approve")).toBeDisabled();
  expect(screen.getByTestId("draft-reject")).toBeDisabled();
  fireEvent.click(screen.getByTestId("draft-approve"));
  expect(sent).toHaveLength(0);
  expect(screen.getByText(/草稿尚未执行/)).toBeVisible();
});

it("同一草稿不双发；服务端拒绝可见，恢复批准按钮", () => {
  render(<AssistedDraftCard />);
  fireEvent.click(screen.getByTestId("draft-approve"));
  fireEvent.click(screen.getByTestId("draft-approve"));
  expect(sent).toHaveLength(1);
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      type: "request_error",
      payload: {
        command_id: sent[0].command_id,
        code: "revision_conflict",
        message: "世界已变化，请重新确认草稿",
      },
    }),
  );
  expect(screen.getByText(/世界已变化/)).toBeVisible();
  expect(screen.getByTestId("draft-approve")).toBeEnabled();
});

it("失去主持授权时也不能通过原 ID 重发旧主持命令", () => {
  const result = sendKeeperCommand("resolve_draft", {
    draft_id: draft.draftId,
    decision: "approved",
  });
  expect(result.ok).toBe(true);
  if (!result.ok) throw new Error(result.reason);
  useOnlineStore.setState({
    members: [
      {
        user_id: "me",
        username: "我",
        role: "player",
        can_keeper: false,
        investigator: null,
      },
    ],
  });
  expect(resendStructuredRequest(result.requestId).ok).toBe(false);
  expect(sent).toHaveLength(1);
});

it("完整快照未提供草稿时清掉旧秘密，不能恢复上一权限的草稿", () => {
  useStructuredStore
    .getState()
    .applySnapshot({ ...EVENT_FIXTURES.snapshot.payload }, WORLD_ID);
  expect(useStructuredStore.getState().keeperDraft).toBeNull();
});

it("assisted 标志不代替审批命令：缺少 resolve_draft 时仅可查看", () => {
  useStructuredStore.getState().applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
  render(<AssistedDraftCard />);
  expect(screen.getByText(draft.summary)).toBeVisible();
  expect(screen.getByTestId("draft-approve")).toBeDisabled();
  expect(screen.getByTestId("draft-reject")).toBeDisabled();
  expect(screen.getByText(/服务端未开放草稿审批/)).toBeVisible();
  expect(
    sendKeeperCommand("resolve_draft", {
      draft_id: draft.draftId,
      decision: "approved",
    }).ok,
  ).toBe(false);
  expect(sent).toHaveLength(0);
});

it("能力撤回后旧命令不可重发，等待中的草稿不被伪造为完成", () => {
  render(<AssistedDraftCard />);
  fireEvent.click(screen.getByTestId("draft-approve"));
  const commandId = String(sent[0].command_id);
  act(() =>
    useStructuredStore
      .getState()
      .applyCapabilities(STRUCTURED_CAPABILITIES_WIRE),
  );
  expect(screen.getByText("审批中")).toBeVisible();
  expect(screen.getByTestId("draft-approve")).toBeDisabled();
  expect(resendStructuredRequest(commandId).ok).toBe(false);
  expect(sent).toHaveLength(1);
});

it("匹配的收件回执不是审批结算；已结算但投影未更新时不重复审批", () => {
  render(<AssistedDraftCard />);
  fireEvent.click(screen.getByTestId("draft-approve"));
  const commandId = sent[0].command_id;
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      type: "action_ack",
      payload: { command_id: commandId, status: "queued" },
    }),
  );
  expect(screen.getByText("服务端已收件，等待审批结算。")).toBeVisible();
  expect(screen.getByText("审批中")).toBeVisible();
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      payload: { command_id: commandId, status: "completed" },
    }),
  );
  expect(screen.getByText("已结算")).toBeVisible();
  expect(screen.getByText("服务端已结算，等待草稿列表更新。")).toBeVisible();
  expect(screen.getByTestId("draft-approve")).toBeDisabled();
  expect(screen.getByTestId("draft-reject")).toBeDisabled();
  fireEvent.click(screen.getByTestId("draft-approve"));
  expect(sent).toHaveLength(1);
});

it("失败详情可读而不是静默恢复按钮", () => {
  render(<AssistedDraftCard />);
  fireEvent.click(screen.getByTestId("draft-reject"));
  act(() =>
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.actionCompleted,
      payload: {
        command_id: sent[0].command_id,
        status: "failed",
        detail: "本次审批未完成，请确认草稿后重试",
      },
    }),
  );
  expect(screen.getByText(/本次审批未完成/)).toBeVisible();
  expect(screen.getByTestId("draft-reject")).toBeEnabled();
});
