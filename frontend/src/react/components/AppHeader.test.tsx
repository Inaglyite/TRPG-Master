import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../../state/app-store";
import { AppHeader } from "./AppHeader";

describe("AppHeader", () => {
  beforeEach(() => {
    useAppStore.setState({ connection: "connecting", title: "疯狂宅邸" });
  });

  it("reacts to connection and module theme state", () => {
    const { container } = render(<AppHeader />);
    expect(screen.getByRole("heading")).toHaveTextContent("疯狂宅邸");
    expect(container.querySelector("#conn-status")).toHaveClass("connecting");

    act(() => {
      useAppStore.getState().setConnection("connected");
      useAppStore.getState().setTitle("猩红文档");
    });

    expect(screen.getByRole("heading")).toHaveTextContent("猩红文档");
    expect(container.querySelector("#conn-status")).toHaveClass("connected");
  });
});
