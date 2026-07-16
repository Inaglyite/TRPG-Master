import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useStartStore } from "../state/start-store";
import { GameShell } from "./GameShell";

describe("GameShell layout ownership", () => {
  beforeEach(() => useStartStore.setState({ gameStarted: true }));

  it("keeps handouts inside the chat positioning context", () => {
    const { container } = render(<GameShell />);
    const chat = container.querySelector<HTMLElement>("#chat-panel");
    const handouts = container.querySelector<HTMLElement>("#handout-container");
    expect(chat).toContainElement(handouts);
  });
});
