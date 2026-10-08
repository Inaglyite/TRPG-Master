import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { STRUCTURED_CAPABILITIES } from "../../../protocol/structured-fixtures";
import { useAppStore } from "../../../state/app-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { StructuredToolRow } from "./StructuredToolRow";

vi.mock("./StructuredCards", () => ({ RollDialog: () => <div>掷骰窗口</div> }));

beforeEach(() => {
  useAppStore.setState({
    mode: "online",
    connection: "connected",
    dialog: null,
    ending: null,
    choices: [],
  });
  useStructuredStore.setState({
    ...initialStructuredState,
    capabilities: STRUCTURED_CAPABILITIES,
  });
  useOnlineStore.setState({
    ...initialOnlineState,
    user: { id: "alice", username: "爱丽丝" },
    members: [{ user_id: "alice", username: "爱丽丝", role: "player" }],
  });
});

describe("ordinary roll identity gate", () => {
  it("an unclaimed keeper cannot open a player roll", () => {
    render(<StructuredToolRow />);
    expect(screen.getByRole("button", { name: "普通掷骰" })).toBeDisabled();
    expect(screen.getByText("调查员普通骰需先认领角色。")).toBeVisible();
  });
  it("a viewer remains read-only even with a stale previous investigator ID", () => {
    useStructuredStore.setState({
      identity: {
        ...initialStructuredState.identity,
        investigatorId: "old-pc",
      },
    });
    useOnlineStore.setState({
      members: [{ user_id: "alice", username: "爱丽丝", role: "viewer" }],
    });
    render(<StructuredToolRow />);
    expect(screen.getByRole("button", { name: "普通掷骰" })).toBeDisabled();
    expect(screen.getByText(/旁观模式只能查看公开叙事/)).toBeVisible();
  });
  it("a controlled investigator can open the roll without any model", () => {
    useStructuredStore.setState({
      identity: { ...initialStructuredState.identity, investigatorId: "pc" },
    });
    render(<StructuredToolRow />);
    fireEvent.click(screen.getByRole("button", { name: "普通掷骰" }));
    expect(screen.getByText("掷骰窗口")).toBeVisible();
  });
});
