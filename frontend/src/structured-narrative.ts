import type { StructuredEventEnvelope } from "./protocol/structured";
import {
  useMessageStore,
  type ChatMessage,
  type Speaker,
} from "./state/message-store";

/**
 * Structured messages have their own IDs and explicit attribution. Do not put
 * them in the legacy turn's shared typewriter queue: independent messages can
 * interleave, and completion is authoritative rather than another text chunk.
 */
export function projectNarrativeMessage(
  envelope: StructuredEventEnvelope,
  incomingSpeaker?: Speaker,
): void {
  const payload = envelope.payload;
  const messageId = payload.message_id;
  if (typeof messageId !== "string" || !messageId) return;
  const id = `structured:${envelope.world_id}:${messageId}`;
  let changed = false;
  useMessageStore.getState().updateMessages((messages) => {
    const previous = messages.find((message) => message.id === id);
    // Reconnect replay and late chunks must not reopen settled messages.
    if (previous?.streaming === false) return messages;
    const speaker = incomingSpeaker ?? previous?.speaker;
    const incomingText = typeof payload.text === "string" ? payload.text : "";
    const body =
      envelope.type === "message_completed"
        ? incomingText
        : (previous?.text ?? "") +
          (envelope.type === "message_chunk" ? incomingText : "");
    const kind =
      speaker?.type === "investigator"
        ? "player"
        : speaker?.type === "system"
          ? "system"
          : "gm";
    const next: ChatMessage = {
      id,
      kind,
      text: body,
      speaker,
      streaming: envelope.type !== "message_completed",
      canBranch: false,
      canRewrite: false,
      ...(kind === "gm" && speaker
        ? {
            segments: [
              {
                kind: speaker.type === "npc" ? "speech" : "narration",
                text: body,
                speaker,
              },
            ],
          }
        : {}),
    };
    changed = true;
    // The first event fixes ordering; interleaved chunks update that same slot.
    return previous
      ? messages.map((message) => (message.id === id ? next : message))
      : [...messages, next];
  });
  if (changed) useMessageStore.getState().requestScroll();
}
