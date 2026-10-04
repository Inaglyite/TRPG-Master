import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { applyStructuredEffects } from "../structured-effects";
import { loadStructuredAsset } from "../api/structuredAssets";
import { showHandout, updateCharPanel } from "../panels";
import { useAppStore } from "./app-store";
import { initialStructuredState, useStructuredStore } from "./structured-store";
import { useMessageStore } from "./message-store";
import type { StructuredEventEnvelope } from "../protocol/structured";

vi.mock("../api/structuredAssets", () => ({ loadStructuredAsset: vi.fn() }));
vi.mock("../panels", () => ({
  showHandout: vi.fn(),
  updateCharPanel: vi.fn(),
}));
vi.mock("../renderer", () => ({
  onDice: vi.fn(),
  onNarrativeChunk: vi.fn(),
  onNarrativeSegment: vi.fn(),
  resetGamePresentation: vi.fn(),
}));
const image = {
  asset_id: "photo",
  label: "旧书房",
  asset_data_uri: "data:image/png;base64,aA==",
};
const event: StructuredEventEnvelope = {
  protocol_version: 1,
  event_id: 1,
  world_id: "world-1",
  sequence: 1,
  revision: 1,
  type: "handout_presented",
  cause_request_id: "cmd",
  payload: { asset_id: "photo", investigator_id: "alice" },
};

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ mode: "online" });
  useStructuredStore.setState({
    ...initialStructuredState,
    identity: { ...initialStructuredState.identity, worldId: "world-1" },
  });
  useMessageStore.setState({ messages: [] });
});

describe("authorized structured image delivery", () => {
  it("an old image response cannot reappear after clearing authorization and re-entering the same world", async () => {
    let done!: (value: typeof image) => void;
    vi.mocked(loadStructuredAsset).mockReturnValue(
      new Promise((resolve) => {
        done = resolve;
      }),
    );
    applyStructuredEffects(event);
    useStructuredStore.getState().reset();
    useStructuredStore.getState().bindWorld("world-1", 1);
    done(image);
    await Promise.resolve();
    expect(showHandout).not.toHaveBeenCalled();
  });

  it("loads an image only after a committed handout event, outside the event log", async () => {
    vi.mocked(loadStructuredAsset).mockResolvedValue(image);
    applyStructuredEffects(event);
    await waitFor(() =>
      expect(showHandout).toHaveBeenCalledWith(
        expect.objectContaining({ asset_data_uri: image.asset_data_uri }),
      ),
    );
    expect(loadStructuredAsset).toHaveBeenCalledWith("world-1", "photo", false);
  });

  it("discards a late image when the viewing world has changed", async () => {
    let done!: (value: typeof image) => void;
    vi.mocked(loadStructuredAsset).mockReturnValue(
      new Promise((resolve) => {
        done = resolve;
      }),
    );
    applyStructuredEffects(event);
    useStructuredStore.getState().bindWorld("world-2", 1);
    done(image);
    await Promise.resolve();
    expect(showHandout).not.toHaveBeenCalled();
  });

  it("reports a read failure visibly and does not pretend to display a picture", async () => {
    vi.mocked(loadStructuredAsset).mockRejectedValue(new Error("no grant"));
    applyStructuredEffects(event);
    await waitFor(() =>
      expect(useMessageStore.getState().messages[0]?.text).toContain(
        "图片读取失败",
      ),
    );
    expect(showHandout).not.toHaveBeenCalled();
  });
});

describe("owned character projection", () => {
  it("an explicit null identity revokes the prior character binding", () => {
    useStructuredStore.getState().setInvestigator("alice");
    useStructuredStore
      .getState()
      .applySnapshot({ investigator_id: null }, "world-1");
    expect(useStructuredStore.getState().identity.investigatorId).toBe("");
  });
  it("restores the authorized card on start/reconnect and clears it for a non-investigator", () => {
    const character = {
      name: "爱丽丝",
      hp: 11,
      max_hp: 12,
      san: 58,
      max_san: 65,
    };
    applyStructuredEffects({
      ...event,
      type: "session_snapshot",
      payload: { character },
    });
    expect(updateCharPanel).toHaveBeenCalledWith(JSON.stringify(character));
    useAppStore.setState({ character });
    applyStructuredEffects({
      ...event,
      type: "session_snapshot",
      payload: { character: null },
    });
    expect(useAppStore.getState().character).toBeNull();
  });

  it("another investigator's public stat update cannot change the current player's card", () => {
    useStructuredStore.setState({
      identity: {
        ...initialStructuredState.identity,
        worldId: "world-1",
        investigatorId: "alice",
      },
    });
    useAppStore.setState({ character: { name: "爱丽丝", san: 58 } });
    applyStructuredEffects({
      ...event,
      type: "state_changed",
      payload: { investigator_id: "bob", san: 20 },
    });
    expect(useAppStore.getState().character?.san).toBe(58);
    applyStructuredEffects({
      ...event,
      type: "state_changed",
      payload: { investigator_id: "alice", san: 57 },
    });
    expect(useAppStore.getState().character?.san).toBe(57);
  });
});

it("uses the explicit NPC ID and public roster name instead of guessing from dialogue", () => {
  useStructuredStore.setState({
    targets: [{ id: "doctor", kind: "npc", name: "惠特克罗夫医生" }],
  });
  applyStructuredEffects({
    ...event,
    type: "message_completed",
    payload: {
      message_id: "doctor-response",
      speaker: { kind: "npc", id: "doctor" },
      text: "您想先看遗体吗？",
    },
  });
  expect(useMessageStore.getState().messages).toMatchObject([
    {
      text: "您想先看遗体吗？",
      streaming: false,
      segments: [
        {
          kind: "speech",
          speaker: {
            type: "npc",
            id: "doctor",
            name: "惠特克罗夫医生",
          },
        },
      ],
    },
  ]);
});

it("does not render recovered text twice when its completion is replayed", () => {
  applyStructuredEffects({
    ...event,
    type: "session_snapshot",
    payload: {
      message_history: {
        messages: [
          {
            message_id: "recovered",
            sequence: 1,
            text: "旧叙事",
            speaker: { kind: "keeper", name: "守秘人" },
          },
        ],
        next_before_sequence: null,
      },
    },
  });
  applyStructuredEffects({
    ...event,
    type: "message_completed",
    payload: {
      message_id: "recovered",
      text: "旧叙事",
      speaker: { kind: "keeper" },
    },
  });
  expect(useMessageStore.getState().messages).toHaveLength(1);
  expect(useMessageStore.getState().messages[0]).toMatchObject({
    id: "structured:world-1:recovered",
    text: "旧叙事",
    streaming: false,
  });
  expect(
    useMessageStore.getState().messages.map((message) => message.text),
  ).toEqual(["旧叙事"]);
});
