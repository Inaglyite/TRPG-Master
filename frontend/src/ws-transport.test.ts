import { beforeEach, describe, expect, it, vi } from "vitest";

import { recoverLatestTurn, safeSend, setActiveTransport } from "./ws";

describe("ws transport adapter", () => {
  beforeEach(() => {
    setActiveTransport(null);
    vi.clearAllMocks();
  });

  it("设置 transport 后 safeSend 全部改走 transport", () => {
    const transport = { send: vi.fn() };
    setActiveTransport(transport);
    safeSend(JSON.stringify({ type: "action", text: "检查门锁" }));
    safeSend(JSON.stringify({ type: "ping" }));
    expect(transport.send).toHaveBeenCalledTimes(2);
    expect(transport.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "action", text: "检查门锁" }),
    );
  });

  it("recoverLatestTurn 在 transport 模式下也能发送 save_load", () => {
    const transport = { send: vi.fn() };
    setActiveTransport(transport);
    recoverLatestTurn();
    expect(transport.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "save_load", slot_id: "slot_000" }),
    );
  });

  it("清除 transport 后恢复单机行为（连接缺失时进入队列，不抛错）", () => {
    const transport = { send: vi.fn() };
    setActiveTransport(transport);
    setActiveTransport(null);
    expect(() => safeSend(JSON.stringify({ type: "ping" }))).not.toThrow();
    expect(transport.send).not.toHaveBeenCalled();
  });
});
