import { describe, expect, it } from "vitest";

import { inviteStatusLabel, roomStatusLabel } from "./room-status";

describe("roomStatusLabel", () => {
  it("已知协议值映射为中文产品文案", () => {
    expect(roomStatusLabel("lobby")).toBe("大厅");
    expect(roomStatusLabel("starting")).toBe("开场中");
    expect(roomStatusLabel("playing")).toBe("游戏中");
  });

  it("未知协议值原样回退（不空白、不崩溃）", () => {
    expect(roomStatusLabel("waiting")).toBe("waiting");
    expect(roomStatusLabel("")).toBe("");
  });
});

describe("inviteStatusLabel", () => {
  it("覆盖后端 list_invites 的全部状态", () => {
    expect(inviteStatusLabel("active")).toBe("有效");
    expect(inviteStatusLabel("revoked")).toBe("已撤销");
    expect(inviteStatusLabel("expired")).toBe("已过期");
    expect(inviteStatusLabel("exhausted")).toBe("已用尽");
  });

  it("未知状态原样回退", () => {
    expect(inviteStatusLabel("pending")).toBe("pending");
  });
});
