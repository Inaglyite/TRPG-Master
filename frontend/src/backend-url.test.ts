import { describe, expect, it } from "vitest";

import {
  backendHttpOrigin,
  backendWebSocketUrl,
  defaultBackendHttpOrigin,
} from "./backend-url";

describe("backend URL", () => {
  it("derives matching HTTP and WebSocket endpoints", () => {
    expect(backendHttpOrigin()).toBe("http://localhost:8765");
    expect(backendWebSocketUrl()).toBe("ws://localhost:8765/ws");
  });

  it("生产云端沿用包含反代端口的页面 origin", () => {
    expect(
      defaultBackendHttpOrigin(
        {
          protocol: "https:",
          hostname: "20.249.11.57",
          origin: "https://20.249.11.57:8443",
        },
        false,
      ),
    ).toBe("https://20.249.11.57:8443");
  });

  it("开发页面仍直连本地后端端口", () => {
    expect(
      defaultBackendHttpOrigin(
        {
          protocol: "http:",
          hostname: "localhost",
          origin: "http://localhost:5173",
        },
        true,
      ),
    ).toBe("http://localhost:8765");
  });
});
