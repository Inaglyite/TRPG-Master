import { create } from "zustand";

const STORAGE_KEY = "trpg-show-action-hints";

export function readActionHintsPreference(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

const REASONING_KEY = "trpg-show-reasoning";

export function readReasoningPreference(): boolean {
  try {
    return localStorage.getItem(REASONING_KEY) === "true";
  } catch {
    return false;
  }
}

export const useGamePreferences = create<{
  showActionHints: boolean;
  showReasoning: boolean;
  setShowReasoning: (show: boolean) => void;
  setShowActionHints: (show: boolean) => void;
}>((set) => ({
  showActionHints: readActionHintsPreference(),
  showReasoning: readReasoningPreference(),
  setShowReasoning(show) {
    try {
      localStorage.setItem(REASONING_KEY, String(show));
    } catch {
      /* session preference remains usable */
    }
    set({ showReasoning: show });
  },
  setShowActionHints(show) {
    try {
      localStorage.setItem(STORAGE_KEY, String(show));
    } catch {
      // Blocked storage must not prevent changing this session's display.
    }
    set({ showActionHints: show });
  },
}));
