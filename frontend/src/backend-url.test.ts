import { describe, expect, it } from "vitest";

import { backendHttpOrigin, backendWebSocketUrl } from "./backend-url";

describe("backend URL", () => {
  it("derives matching HTTP and WebSocket endpoints", () => {
    expect(backendHttpOrigin()).toBe("http://localhost:8765");
    expect(backendWebSocketUrl()).toBe("ws://localhost:8765/ws");
  });
});
