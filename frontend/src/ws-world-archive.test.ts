import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import {
  announceSoloWorldSwitch,
  handleServerPayload,
  setActiveTransport,
} from "./ws";

let sent: ReturnType<typeof vi.fn>;

function lastMessage() {
  const messages = useMessageStore.getState().messages;
  return messages[messages.length - 1];
}

beforeEach(() => {
  sent = vi.fn();
  setActiveTransport({ send: sent });
  useMessageStore.setState({ messages: [] });
  useAppStore.setState({
    mode: "local",
    savePanelOpen: true,
    activeWorldId: "root",
    worlds: [
      { world_id: "root", label: "主时间线", active: true },
      { world_id: "branch-a", label: "岔路", is_branch: true },
    ],
  });
});

afterEach(() => {
  setActiveTransport(null);
});

describe("本地时间线归档回包", () => {
  it("成功后保留面板、移除分支并刷新权威时间线列表", () => {
    handleServerPayload({ type: "world_archived", world_id: "branch-a" });

    expect(useAppStore.getState().savePanelOpen).toBe(true);
    expect(
      useAppStore.getState().worlds.map((world) => world.world_id),
    ).toEqual(["root"]);
    expect(sent).toHaveBeenCalledWith(JSON.stringify({ type: "world_list" }));
    expect(lastMessage()).toMatchObject({
      kind: "system",
      text: "时间线已删除。",
    });
  });

  it("失败时保留面板和时间线，并展示服务端错误", () => {
    handleServerPayload({
      type: "world_archive_failed",
      world_id: "branch-a",
      message: "当前分支仍在使用，不能删除。",
    });

    expect(useAppStore.getState().savePanelOpen).toBe(true);
    expect(
      useAppStore.getState().worlds.map((world) => world.world_id),
    ).toEqual(["root", "branch-a"]);
    expect(sent).not.toHaveBeenCalled();
    expect(lastMessage()).toMatchObject({
      kind: "error",
      text: "当前分支仍在使用，不能删除。",
    });
  });
});

describe("云端单人时间线切换提示", () => {
  it("切换/建分支提示新时间线名；redirect 静默重连不提示", () => {
    announceSoloWorldSwitch("分支A", "switched");
    expect(lastMessage()).toMatchObject({
      kind: "system",
      text: "已切换到时间线「分支A」。",
    });
    announceSoloWorldSwitch("", "redirect");
    expect(lastMessage()).toMatchObject({
      kind: "system",
      text: "已切换到时间线「分支A」。",
    });
  });
});
