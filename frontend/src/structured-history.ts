import {
  narrativeHistorySchema,
  type NarrativeHistoryPage,
} from "./api/structuredHistory";
import { useMessageStore, type ChatMessage } from "./state/message-store";
import { resetGamePresentation } from "./renderer";

export function historyMessages(
  worldId: string,
  page: NarrativeHistoryPage,
): ChatMessage[] {
  return page.messages.map((message) => {
    const speaker = {
      type: message.speaker.kind,
      id: message.speaker.id,
      name: message.speaker.name,
    };
    const kind =
      speaker.type === "investigator"
        ? "player"
        : speaker.type === "system"
          ? "system"
          : "gm";
    return {
      id: `structured:${worldId}:${message.message_id}`,
      kind,
      text: message.text,
      ...(message.entry_kind === "action_request"
        ? { entryKind: "action_request" as const }
        : {}),
      speaker,
      ...(kind === "gm"
        ? {
            segments: [
              {
                kind: speaker.type === "npc" ? "speech" : "narration",
                text: message.text,
                speaker,
              },
            ] as ChatMessage["segments"],
          }
        : {}),
      streaming: false,
      canBranch: false,
      canRewrite: false,
    };
  });
}

/** Authority comes from the authenticated snapshot, not cached local bubbles. */
export function restoreNarrativeHistory(worldId: string, value: unknown): void {
  const page = narrativeHistorySchema.safeParse(value);
  if (!page.success) return;
  // Discard, never flush, stale playback when replacing the authoritative view.
  // Future queued text must not reappear after loading an earlier checkpoint.
  resetGamePresentation();
  useMessageStore
    .getState()
    .replaceMessages(historyMessages(worldId, page.data));
}

export function prependNarrativeHistory(
  worldId: string,
  page: NarrativeHistoryPage,
): void {
  const older = historyMessages(worldId, page);
  useMessageStore.setState((state) => {
    const existing = new Set(state.messages.map((message) => message.id));
    const added = older.filter((message) => !existing.has(message.id));
    return {
      messages: [...added, ...state.messages],
      historyPrependRequest:
        state.historyPrependRequest + (added.length ? 1 : 0),
    };
  });
}
