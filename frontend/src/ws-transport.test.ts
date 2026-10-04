import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  recoverLatestTurn,
  safeSend,
  sendImmediately,
  setActiveTransport,
} from "./ws";

describe("ws transport adapter", () => {
  beforeEach(() => {
    setActiveTransport(null);
    vi.clearAllMocks();
  });

  it("即时发送无通道时拒绝，不借用可排队的 send", () => {
    const send = vi.fn();
    setActiveTransport({ send });
    expect(sendImmediately("private-write")).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });

  it("即时发送只使用 sendNow，传输异常可诊断为未发送", () => {
    const send = vi.fn();
    const sendNow = vi
      .fn()
      .mockReturnValueOnce(true)
      .mockImplementationOnce(() => {
        throw new Error("closed");
      });
    setActiveTransport({ send, sendNow });
    expect(sendImmediately("private-write")).toBe(true);
    expect(sendImmediately("private-write")).toBe(false);
    expect(send).not.toHaveBeenCalled();
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
