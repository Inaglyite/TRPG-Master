import { create } from "zustand";
import { useAppStore } from "./app-store";
import { useGamePreferences } from "./game-preferences";
import { useMessageStore } from "./message-store";

export type ReasoningFrame = {
  type: "model_reasoning";
  phase: "start" | "delta" | "end";
  request_id: string;
  model: string;
  role: string;
  world_id?: string;
  turn_id?: string;
  text?: string;
  status?: "completed" | "interrupted";
  truncated?: boolean;
};
export type ReasoningEntry = {
  id: string;
  model: string;
  role: string;
  text: string;
  anchor: { messageId: string; side: "before" | "after" } | null;
  status: "running" | "completed" | "interrupted";
  truncated: boolean;
};
const LIMIT = 65536;

export const useReasoningStore = create<{
  entries: ReasoningEntry[];
  activeTurnId: string | null;
  reset: (turnId?: string | null) => void;
  receive: (frame: ReasoningFrame) => void;
}>((set, get) => ({
  entries: [],
  activeTurnId: null,
  reset: (turnId = null) => set({ entries: [], activeTurnId: turnId }),
  receive(frame) {
    if (!useGamePreferences.getState().showReasoning) return;
    if (
      frame.world_id &&
      frame.world_id !== useAppStore.getState().activeWorldId
    )
      return;
    if (
      frame.turn_id &&
      get().activeTurnId &&
      frame.turn_id !== get().activeTurnId
    )
      return;
    set((state) => {
      const existing = state.entries.find(
        (entry) => entry.id === frame.request_id,
      );
      if (
        frame.phase === "start" ||
        (!existing && frame.phase === "delta" && frame.text)
      ) {
        if (existing) return state;
        const messages = useMessageStore.getState().messages;
        // Loading and roll placeholders disappear as soon as narration arrives.
        const rewrite = messages.find((m) => m.rewriteTarget && !m.hidden);
        const last = [...messages]
          .reverse()
          .find(
            (m) =>
              !m.hidden && m.kind !== "loading" && m.kind !== "roll-pending",
          );
        const message = rewrite ?? last;
        const anchor: ReasoningEntry["anchor"] = message
          ? {
              messageId: message.id,
              side:
                message.kind === "gm" && message.streaming ? "before" : "after",
            }
          : null;
        return {
          entries: [
            ...state.entries,
            {
              id: frame.request_id,
              anchor,
              model: frame.model,
              role: frame.role,
              text: (frame.text || "").slice(0, LIMIT),
              status: "running" as const,
              truncated: (frame.text?.length || 0) > LIMIT,
            },
          ].slice(-8),
        };
      }
      if (!existing || existing.status !== "running") return state;
      return {
        entries: state.entries.map((entry) =>
          entry.id !== frame.request_id
            ? entry
            : frame.phase === "delta"
              ? {
                  ...entry,
                  text: (entry.text + (frame.text || "")).slice(0, LIMIT),
                  truncated:
                    entry.truncated ||
                    entry.text.length + (frame.text?.length || 0) > LIMIT,
                }
              : {
                  ...entry,
                  status: frame.status || "completed",
                  truncated: entry.truncated || Boolean(frame.truncated),
                },
        ),
      };
    });
  },
}));

useGamePreferences.subscribe((state, previous) => {
  if (!state.showReasoning && previous.showReasoning)
    useReasoningStore.getState().reset();
});
useAppStore.subscribe((state, previous) => {
  if (
    state.activeWorldId !== previous.activeWorldId ||
    (state.connection === "disconnected" &&
      previous.connection !== "disconnected")
  )
    useReasoningStore.getState().reset();
});
