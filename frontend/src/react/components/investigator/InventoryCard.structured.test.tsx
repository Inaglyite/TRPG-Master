import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import { STRUCTURED_CAPABILITIES_WIRE } from "../../../protocol/structured-fixtures";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";
import { InventoryCard } from "./InventoryCard";

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useStructuredStore.getState().applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
  useInvestigatorPanelStore.setState({
    worldId: null,
    prefsByWorld: {},
    editor: null,
  });
});

it("hides a consumed zero-quantity stack instead of showing a ghost usable item", () => {
  useStructuredStore.setState({
    items: [{ id: "consumed", label: "旧绷带", quantity: 0, operations: [] }],
  });
  render(<InventoryCard />);
  expect(screen.queryByText("旧绷带")).toBeNull();
  expect(screen.queryByRole("button", { name: /^使用$/ })).toBeNull();
  expect(screen.getByText("暂无可使用的随身道具")).toBeInTheDocument();
});

it("keeps a remaining positive stack and its exact quantity visible", () => {
  useStructuredStore.setState({
    items: [{ id: "remaining", label: "绷带", quantity: 2, operations: [] }],
  });
  render(<InventoryCard />);
  expect(screen.getByText("绷带")).toBeInTheDocument();
  expect(screen.getByText("×2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^使用$/ })).toBeInTheDocument();
  expect(screen.queryByText("暂无可使用的随身道具")).toBeNull();
});
