import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  useGamePreferences,
  withGenerationPreferences,
} from "./state/game-preferences";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import {
  finishNarrativeStream,
  flushNarrativeStream,
  onNarrativeChunk,
  resetGamePresentation,
} from "./renderer";
import { onDone } from "./options";
import { safeSend, sendImmediately, setActiveTransport } from "./ws";

beforeEach(() => {
  vi.useFakeTimers();
  resetGamePresentation();
  useMessageStore.setState({ scrollRequest: 0 });
  useGamePreferences.setState({ showActionHints: true });
  useAppStore.setState({ inputEnabled: false, choices: [], mode: "local" });
  setActiveTransport(null);
});
afterEach(() => {
  flushNarrativeStream();
  finishNarrativeStream();
  useGamePreferences.setState({ showActionHints: true });
  setActiveTransport(null);
  vi.useRealTimers();
});

it("sends the disabled preference on legacy generation only, including instant sends", () => {
  const send = vi.fn();
  const sendNow = vi.fn((_payload: string) => true);
  setActiveTransport({ send, sendNow });
  useGamePreferences.setState({ showActionHints: false });
  for (const type of [
    "start",
    "action",
    "continue",
    "save_load",
    "turn_rewrite",
  ]) {
    safeSend(JSON.stringify({ type }));
    expect(JSON.parse(send.mock.calls.at(-1)![0])).toMatchObject({
      type,
      generate_action_hints: false,
    });
    expect(sendImmediately(JSON.stringify({ type }))).toBe(true);
    expect(JSON.parse(sendNow.mock.calls.at(-1)![0])).toMatchObject({
      type,
      generate_action_hints: false,
    });
  }
  const structured = JSON.stringify({
    type: "action_request",
    action: { kind: "freeform", text: "检查" },
  });
  expect(withGenerationPreferences(structured)).toBe(structured);
  expect(withGenerationPreferences('{"type":"decision_reply"}')).toBe(
    '{"type":"decision_reply"}',
  );
  useGamePreferences.setState({ showActionHints: true });
  expect(withGenerationPreferences('{"type":"action"}')).toBe(
    '{"type":"action"}',
  );
});

const hiddenTail = "\n\n**你可以——**\n1. " + "一条很长的建议行动".repeat(120);
describe("hidden menu playback", () => {
  it("finishes invisible menu text without keeping input locked or scrolling every character", () => {
    useGamePreferences.setState({ showActionHints: false });
    onNarrativeChunk("雨声。" + hiddenTail);
    onDone([]);
    vi.advanceTimersByTime(1000);
    expect(useAppStore.getState().inputEnabled).toBe(true);
    expect(useMessageStore.getState().messages.at(-1)?.streaming).toBe(false);
    expect(useMessageStore.getState().messages.at(-1)?.text).toContain(
      hiddenTail,
    );
    expect(useMessageStore.getState().scrollRequest).toBeLessThan(10);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps visible prose and NPC speech on the normal playback schedule", () => {
    useGamePreferences.setState({ showActionHints: false });
    onNarrativeChunk("这是一段确实需要展示的台词。".repeat(90), "npc");
    onDone([]);
    vi.advanceTimersByTime(1000);
    expect(useAppStore.getState().inputEnabled).toBe(false);
    flushNarrativeStream();
    expect(useAppStore.getState().inputEnabled).toBe(true);
  });

  it("releases an already queued menu after the preference is turned off", () => {
    onNarrativeChunk("雨声。" + hiddenTail);
    onDone([]);
    vi.advanceTimersByTime(1000);
    expect(useAppStore.getState().inputEnabled).toBe(false);
    useGamePreferences.setState({ showActionHints: false });
    vi.advanceTimersByTime(300);
    expect(useAppStore.getState().inputEnabled).toBe(true);
  });
});
