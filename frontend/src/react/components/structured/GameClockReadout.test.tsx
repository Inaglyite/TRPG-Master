import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useAppStore } from "../../../state/app-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { GameClockReadout } from "./GameClockReadout";

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({ connection: "connected" });
});

describe("readonly clock", () => {
  it("unknown is not zero; a snapshot shows zero and later actual minutes", () => {
    render(<GameClockReadout variant="header" />);
    expect(screen.getByTestId("header-game-clock")).toHaveTextContent("未提供");
    act(() => useStructuredStore.setState({ clockMinutes: 0 }));
    expect(screen.getByTestId("header-game-clock")).toHaveTextContent(
      "已过0分钟",
    );
    act(() => useStructuredStore.setState({ clockMinutes: 200 }));
    expect(screen.getByTestId("header-game-clock")).toHaveTextContent(
      "已过3小时20分钟",
    );
    act(() => useAppStore.setState({ connection: "disconnected" }));
    expect(screen.getByTestId("header-game-clock")).toHaveTextContent(
      "同步中，上次记录3小时20分钟",
    );
  });
  it("keeper reference is readonly and uses the same state", () => {
    useStructuredStore.setState({ clockMinutes: 1440 });
    render(<GameClockReadout variant="reference" />);
    expect(screen.getByTestId("reference-game-clock")).toHaveTextContent(
      "已过1天",
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });
});
