import { create } from "zustand";

/**
 * 顶栏“当前场景”行的状态。
 *
 * 位置只有一个权威来源：服务端已提交的世界状态（世界的 `current_scene`）。
 * 这里只保存服务端投影过来的**玩家可见地点名**，不保存场景 id、不保存第二份可写
 * 位置，也绝不从叙事正文或关键词推断。点击“前往”、正文写到某个地点、玩家提到地名
 * 都不会经过这里。
 *
 * `worldId` 用于两件事：
 * - 切换世界时立刻清空旧地点（进入 `syncing`），不等新世界的第一条消息；
 * - 丢弃旧世界的迟到消息（例如切世界前发出的 `state_data`），避免把上一个地点
 *   写到新世界的标题上。
 *
 * 未被服务端告知过任何世界时 `worldId` 为空串，此时第一条带世界标识的消息会被采纳。
 */
export type SceneStatus = "syncing" | "known" | "unknown";

export type SceneState = {
  worldId: string;
  status: SceneStatus;
  name: string;
};

type SceneActions = {
  /** 世界标识变化时清空地点；标识未变则保持原样，避免同一世界的重复同步抖动。 */
  setWorld: (worldId: string) => void;
  /** 应用服务端场景投影；世界不匹配或载荷缺字段时不做任何改动。 */
  applyScene: (worldId: unknown, scene: unknown) => void;
  reset: () => void;
};

const MAX_NAME_LENGTH = 120;

function cleanName(raw: unknown): string {
  if (typeof raw !== "string") return "";
  return raw.replace(/\s+/g, " ").trim().slice(0, MAX_NAME_LENGTH);
}

export const useSceneStore = create<SceneState & SceneActions>((set) => ({
  worldId: "",
  status: "syncing",
  name: "",
  setWorld: (worldId) =>
    set((state) => {
      const id = String(worldId || "");
      if (!id || id === state.worldId) return state;
      return { worldId: id, status: "syncing", name: "" };
    }),
  applyScene: (worldId, scene) =>
    set((state) => {
      const id = String(worldId || "");
      // 旧世界的迟到事件：当前已绑定另一个世界时直接丢弃，
      // 不能让它把上一个地点写到新世界的标题上。
      if (state.worldId && id && id !== state.worldId) return state;
      // 载荷没有 scene 字段（旧服务端、或不携带场景的消息）时保持现状。
      if (scene === undefined) return state;
      // 服务端的 world_id 是权威标识；本地尚未绑定世界时以它为准，
      // 这样连接初期的第一条状态消息也能直接显示位置。
      const bound = state.worldId || id;
      if (scene === null)
        return { worldId: bound, status: "unknown", name: "" };
      const name = cleanName((scene as { name?: unknown } | null)?.name);
      if (!name) return { worldId: bound, status: "unknown", name: "" };
      return { worldId: bound, status: "known", name };
    }),
  reset: () => set({ worldId: "", status: "syncing", name: "" }),
}));

/** 顶栏那一行要显示的文字。 */
export function sceneLabel(state: Pick<SceneState, "status" | "name">): string {
  if (state.status === "syncing") return "正在同步位置…";
  if (state.status === "unknown") return "位置未知";
  return state.name;
}
