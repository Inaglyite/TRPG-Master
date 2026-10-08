import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import { STRUCTURED_CAPABILITIES } from "../../../protocol/structured-fixtures";
import { setStructuredSender } from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { KeeperDiceTool, KeeperDiceHistory } from "./KeeperDiceTool";

let sent: unknown[];
beforeEach(() => {
  sent = [];
  setStructuredSender((frame) => {
    sent.push(frame);
    return true;
  });
  useAppStore.setState({
    mode: "online",
    connection: "connected",
    inputEnabled: true,
    dialog: null,
    ending: null,
    choices: [],
  });
  useOnlineStore.setState({
    ...initialOnlineState,
    user: { id: "host", username: "主持" },
    members: [
      { user_id: "host", username: "主持", role: "owner", can_keeper: true },
    ],
  });
  useStructuredStore.setState({
    ...initialStructuredState,
    capabilities: {
      ...STRUCTURED_CAPABILITIES,
      commands: [...STRUCTURED_CAPABILITIES.commands, "keeper_roll"],
    },
    identity: {
      ...initialStructuredState.identity,
      worldId: "world",
      revision: 1,
      keeperMode: "human",
      keeperUserId: "host",
    },
  });
});

it("unclaimed human keeper can choose private/public and explicitly send without an investigator", () => {
  render(<KeeperDiceTool />);
  fireEvent.click(screen.getByTestId("btn-keeper-dice"));
  expect(screen.getByRole("combobox", { name: "接收范围" })).toHaveValue(
    "keeper",
  );
  fireEvent.change(screen.getByLabelText("骰式"), {
    target: { value: "2d6+3" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "接收范围" }), {
    target: { value: "public" },
  });
  expect(sent).toHaveLength(0);
  fireEvent.click(screen.getByTestId("keeper-dice-submit"));
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({
    type: "command_request",
    kind: "keeper_roll",
    payload: { spec: "2d6+3", visibility: "public" },
  });
  expect(sent[0]).not.toHaveProperty("investigator_id");
  expect(useStructuredStore.getState().keeperRolls).toEqual([]);
});

it("disconnect, missing capability and another controller do not submit or auto-resend", () => {
  render(<KeeperDiceTool />);
  fireEvent.click(screen.getByTestId("btn-keeper-dice"));
  act(() => useAppStore.setState({ connection: "disconnected" }));
  expect(screen.getByTestId("keeper-dice-submit")).toBeDisabled();
  act(() => useAppStore.setState({ connection: "connected" }));
  expect(sent).toHaveLength(0);
  act(() =>
    useStructuredStore.setState({ capabilities: STRUCTURED_CAPABILITIES }),
  );
  expect(screen.getByTestId("keeper-dice-submit")).toBeDisabled();
  expect(sent).toHaveLength(0);
});

it("owner without keeper rights has no tool and cannot view cached private receipts", () => {
  useOnlineStore.setState({
    members: [
      { user_id: "host", username: "主持", role: "owner", can_keeper: false },
    ],
  });
  useStructuredStore.setState({
    keeperRolls: [
      {
        command_id: "secret",
        visibility: "keeper",
        expression: "1d100",
        dice: [{ sides: 100, values: [27] }],
        total: 27,
        modifier: 0,
      },
    ],
  });
  render(
    <>
      <KeeperDiceTool />
      <KeeperDiceHistory />
    </>,
  );
  expect(screen.queryByTestId("btn-keeper-dice")).toBeNull();
  expect(screen.queryByTestId("keeper-dice-receipt")).toBeNull();
});

it("public result is displayed without skill classification; world switch closes stale dialog", () => {
  useStructuredStore.setState({
    keeperRolls: [
      {
        command_id: "public",
        visibility: "public",
        expression: "1d100",
        dice: [{ sides: 100, values: [27] }],
        total: 27,
        modifier: 0,
      },
    ],
  });
  render(
    <>
      <KeeperDiceTool />
      <KeeperDiceHistory />
    </>,
  );
  expect(screen.getByText("1d100 → 27")).toBeVisible();
  expect(screen.queryByText("检定成功")).toBeNull();
  fireEvent.click(screen.getByTestId("btn-keeper-dice"));
  act(() => useStructuredStore.getState().bindWorld("next"));
  expect(screen.queryByRole("dialog", { name: "主持普通骰" })).toBeNull();
});
