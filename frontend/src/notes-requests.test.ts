import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOTES_RESPONSE_TIMEOUT_MS, NotesRequests } from "./notes-requests";

describe("笔记请求的独立关联与期限组件", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  const scope = { worldId: "world-a", ownerKey: "origin-a/user-a" };
  it("十五秒一次性超时，不自动发送或重试", () => {
    const expired = vi.fn();
    const ledger = new NotesRequests(expired);
    const operation = ledger.begin(scope, "save", "草稿");
    vi.advanceTimersByTime(NOTES_RESPONSE_TIMEOUT_MS);
    expect(expired).toHaveBeenCalledExactlyOnceWith(operation);
    expect(ledger.current).toBeNull();
    vi.advanceTimersByTime(NOTES_RESPONSE_TIMEOUT_MS);
    expect(expired).toHaveBeenCalledTimes(1);
  });
  it("当前请求、世界、账号和来源必须一致", () => {
    const ledger = new NotesRequests(vi.fn());
    const operation = ledger.begin(scope, "save", "草稿");
    const data = {
      world_id: scope.worldId,
      request_id: operation.requestId,
      saved: true,
    };
    expect(ledger.matches(scope, data)).toBe(true);
    expect(ledger.matches({ ...scope, worldId: "world-b" }, data)).toBe(false);
    expect(
      ledger.matches({ ...scope, ownerKey: "origin-b/user-a" }, data),
    ).toBe(false);
    expect(ledger.matches(scope, { ...data, request_id: "old-request" })).toBe(
      false,
    );
    expect(ledger.matches(scope, { ...data, world_id: "world-b" })).toBe(false);
  });
  it("旧服务器读取回包不能代替保存确认，冲突可以匹配后交给上层处理", () => {
    const ledger = new NotesRequests(vi.fn());
    ledger.begin(scope, "save", "草稿");
    expect(ledger.matches(scope, {})).toBe(false);
    expect(ledger.matches(scope, { saved: true })).toBe(true);
    expect(ledger.matches(scope, {}, true)).toBe(true);
  });
  it("完成、取消与替换都清除旧期限，新请求使用新标识", () => {
    const expired = vi.fn();
    const ledger = new NotesRequests(expired);
    const first = ledger.begin(scope, "read");
    const second = ledger.begin(scope, "read");
    expect(second.requestId).not.toBe(first.requestId);
    expect(ledger.finish()).toEqual(second);
    vi.advanceTimersByTime(NOTES_RESPONSE_TIMEOUT_MS);
    expect(expired).not.toHaveBeenCalled();
    ledger.begin(scope, "read");
    ledger.cancel();
    vi.advanceTimersByTime(NOTES_RESPONSE_TIMEOUT_MS);
    expect(expired).not.toHaveBeenCalled();
  });
});
