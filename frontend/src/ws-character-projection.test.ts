import { beforeEach, describe, expect, it } from "vitest";
import { readServerCapabilities } from "./protocol/structured";
import { useAppStore } from "./state/app-store";
import { useSceneStore } from "./state/scene-store";
import { useStartStore } from "./state/start-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "./state/structured-store";
import { handleServerPayload } from "./ws";

const own = { name: "甲", hp: 9, max_hp: 10, san: 58, max_san: 65 };

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({ mode: "local", character: own, clues: {} });
  useStartStore.setState({ gameStarted: true });
  useSceneStore.getState().reset();
});

function structured(supported = true) {
  useStructuredStore.setState({
    capabilities: readServerCapabilities({
      execution_profile: "structured_v1",
      structured_protocol: supported,
      protocol_version: 1,
    }),
  });
}

describe("character projection protocol isolation", () => {
  it("late empty legacy state cannot erase a structured card after reconnect", () => {
    structured();
    handleServerPayload({ type: "state_data", data: "{}", clues: "{}" });
    expect(useAppStore.getState().character).toEqual(own);
  });

  it("legacy full character/clues/scene cannot override structured committed projections", () => {
    structured();
    useSceneStore.getState().setWorld("world-1");
    useSceneStore.getState().applyScene("world-1", { name: "已抵达的停尸房" });
    handleServerPayload({
      type: "character_state",
      data: JSON.stringify({ name: "其他角色", hp: 99 }),
    });
    handleServerPayload({
      type: "state_data",
      world_id: "world-1",
      data: JSON.stringify({ hp: 1 }),
      clues: JSON.stringify({ npc: [{ id: "secret", text: "不是授权线索" }] }),
      scene: { name: "旧场景" },
    });
    expect(useAppStore.getState().character).toEqual(own);
    expect(useAppStore.getState().clues).toEqual({});
    expect(useSceneStore.getState().name).toBe("已抵达的停尸房");
  });

  it("an unsupported structured protocol still must not fall back to legacy character state", () => {
    structured(false);
    handleServerPayload({ type: "character_state", data: "{}" });
    expect(useAppStore.getState().character).toEqual(own);
  });

  it("legacy worlds retain their normal authoritative character and state replies", () => {
    handleServerPayload({
      type: "character_state",
      data: JSON.stringify({ ...own, hp: 8 }),
    });
    expect(useAppStore.getState().character?.hp).toBe(8);
    handleServerPayload({
      type: "state_data",
      data: JSON.stringify({ ...own, hp: 7 }),
      clues: "{}",
    });
    expect(useAppStore.getState().character?.hp).toBe(7);
  });
});
