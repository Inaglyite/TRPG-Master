import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "./state/app-store";
import { sceneLabel, useSceneStore } from "./state/scene-store";
import { useStartStore } from "./state/start-store";
import { handleServerPayload } from "./ws";

function shown(): string {
  return sceneLabel(useSceneStore.getState());
}

beforeEach(() => {
  useSceneStore.getState().reset();
  useAppStore.setState({ mode: "local", title: "TRPG Game" });
  useStartStore.setState({ gameStarted: true });
});

describe("顶栏位置的服务端同步", () => {
  it("state_data 用服务端的权威场景刷新位置", () => {
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      scene: { name: "莱特的办公室" },
      data: "{}",
      clues: "{}",
    });
    expect(shown()).toBe("莱特的办公室");
  });

  it("拒行/取消（场景未变）时位置保持不变", () => {
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      scene: { name: "莱特的办公室" },
    });
    // 服务端拒绝了一次移动：世界状态没变，投影仍是同一场景。
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      scene: { name: "莱特的办公室" },
    });
    expect(shown()).toBe("莱特的办公室");
  });

  it("世界切换时先清空旧地点，再用新世界的投影恢复", () => {
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      scene: { name: "莱特的办公室" },
    });

    handleServerPayload({
      type: "world_context",
      world_id: "world-b",
      module_name: "猩红文档",
    });
    expect(shown()).toBe("正在同步位置…");

    handleServerPayload({
      type: "state_data",
      world_id: "world-b",
      scene: { name: "密斯卡托尼克大学" },
    });
    expect(shown()).toBe("密斯卡托尼克大学");
  });

  it("world_context 自带的场景投影直接生效（换模组/换时间线）", () => {
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      scene: { name: "莱特的办公室" },
    });
    handleServerPayload({
      type: "world_context",
      world_id: "world-b",
      module_name: "另一个模组",
      scene: { name: "门厅" },
    });
    expect(shown()).toBe("门厅");
  });

  it("旧世界迟到的 state_data 不会覆盖新世界的地点", () => {
    handleServerPayload({
      type: "world_context",
      world_id: "world-a",
      module_name: "猩红文档",
      scene: { name: "莱特的办公室" },
    });
    handleServerPayload({
      type: "world_context",
      world_id: "world-b",
      module_name: "猩红文档",
      scene: { name: "密斯卡托尼克大学" },
    });
    // 切世界前发出、切世界后才到达的旧世界状态。
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      scene: { name: "霍布豪斯宅邸" },
    });
    expect(useSceneStore.getState().worldId).toBe("world-b");
    expect(shown()).toBe("密斯卡托尼克大学");
  });

  it("缺 scene 字段的旧服务端消息不改动已显示的地点", () => {
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      scene: { name: "莱特的办公室" },
    });
    handleServerPayload({
      type: "state_data",
      world_id: "world-a",
      data: "{}",
      clues: "{}",
    });
    expect(shown()).toBe("莱特的办公室");
  });
});
