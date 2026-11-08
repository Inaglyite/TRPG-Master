import { create } from "zustand";

const STORAGE_KEY = "trpg-show-action-hints";

export function readActionHintsPreference(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

export const useGamePreferences = create<{
  showActionHints: boolean;
  setShowActionHints: (show: boolean) => void;
}>((set) => ({
  showActionHints: readActionHintsPreference(),
  setShowActionHints(show) {
    try {
      localStorage.setItem(STORAGE_KEY, String(show));
    } catch {
      // Blocked storage must not prevent changing this session's display.
    }
    set({ showActionHints: show });
  },
}));
