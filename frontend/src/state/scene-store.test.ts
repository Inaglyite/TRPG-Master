import { describe, expect, it } from "vitest";

import { sceneLabel, useSceneStore } from "./scene-store";

function reset() {
  useSceneStore.getState().reset();
}

describe("顶栏当前场景状态", () => {
  it("未收到任何投影时显示“正在同步位置…”", () => {
    reset();
    expect(sceneLabel(useSceneStore.getState())).toBe("正在同步位置…");
  });

  it("服务端给了地名就显示地名", () => {
    reset();
    useSceneStore
      .getState()
      .applyScene("world-a", { name: "医学院地下停尸房" });
    expect(sceneLabel(useSceneStore.getState())).toBe("医学院地下停尸房");
    expect(useSceneStore.getState().worldId).toBe("world-a");
  });

  it("服务端明确没有位置时显示“位置未知”而不是留着上一个地名", () => {
    reset();
    useSceneStore.getState().setWorld("world-a");
    useSceneStore.getState().applyScene("world-a", { name: "莱特的办公室" });
    useSceneStore.getState().applyScene("world-a", null);
    expect(sceneLabel(useSceneStore.getState())).toBe("位置未知");
    expect(useSceneStore.getState().name).toBe("");
  });

  it("载荷不带 scene 字段的消息（旧服务端）不改动位置", () => {
    reset();
    useSceneStore.getState().applyScene("world-a", { name: "莱特的办公室" });
    useSceneStore.getState().applyScene("world-a", undefined);
    expect(sceneLabel(useSceneStore.getState())).toBe("莱特的办公室");
  });

  it("切换世界立刻清空旧地点，等新世界的第一条投影", () => {
    reset();
    useSceneStore.getState().applyScene("world-a", { name: "莱特的办公室" });
    useSceneStore.getState().setWorld("world-b");
    expect(useSceneStore.getState().worldId).toBe("world-b");
    expect(sceneLabel(useSceneStore.getState())).toBe("正在同步位置…");
  });

  it("重复收到同一世界的标识不清空已显示的地点", () => {
    reset();
    useSceneStore.getState().applyScene("world-a", { name: "莱特的办公室" });
    useSceneStore.getState().setWorld("world-a");
    expect(sceneLabel(useSceneStore.getState())).toBe("莱特的办公室");
  });

  it("旧世界迟到的投影不会污染新世界的地点", () => {
    reset();
    useSceneStore.getState().applyScene("world-a", { name: "莱特的办公室" });
    useSceneStore.getState().setWorld("world-b");
    // 切世界前排队、切世界后才到达的 state_data。
    useSceneStore.getState().applyScene("world-a", { name: "霍布豪斯宅邸" });
    expect(useSceneStore.getState().worldId).toBe("world-b");
    expect(sceneLabel(useSceneStore.getState())).toBe("正在同步位置…");
  });

  it("本地尚未绑定世界时采纳服务端的世界标识", () => {
    reset();
    useSceneStore.getState().applyScene("world-a", { name: "入口大厅" });
    expect(useSceneStore.getState().worldId).toBe("world-a");
    expect(sceneLabel(useSceneStore.getState())).toBe("入口大厅");
  });

  it("空白或超长的地名按未知/截断处理", () => {
    reset();
    useSceneStore.getState().applyScene("world-a", { name: "   " });
    expect(sceneLabel(useSceneStore.getState())).toBe("位置未知");

    useSceneStore.getState().applyScene("world-a", { name: "  地窖   祭坛  " });
    expect(sceneLabel(useSceneStore.getState())).toBe("地窖 祭坛");

    useSceneStore.getState().applyScene("world-a", { name: "地".repeat(400) });
    expect(sceneLabel(useSceneStore.getState()).length).toBe(120);
  });
});
