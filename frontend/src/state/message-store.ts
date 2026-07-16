import { create } from "zustand";

export type VisualDie = {
  min: number;
  max: number;
  final: number;
  label: string;
  formatter?: "tens";
};

export type ChatMessage = {
  id: string;
  kind: string;
  text: string;
  turnId?: string;
  streaming?: boolean;
  hidden?: boolean;
  rewriteTarget?: boolean;
  dice?: VisualDie[];
  canRewrite?: boolean;
  canBranch?: boolean;
};

type MessageState = {
  messages: ChatMessage[];
  scrollRequest: number;
  forceScrollRequest: number;
  actionReset: number;
  replaceMessages: (messages: ChatMessage[]) => void;
  updateMessages: (updater: (messages: ChatMessage[]) => ChatMessage[]) => void;
  requestScroll: (force?: boolean) => void;
  resetActionButtons: () => void;
};

export const useMessageStore = create<MessageState>((set) => ({
  messages: [],
  scrollRequest: 0,
  forceScrollRequest: 0,
  actionReset: 0,
  replaceMessages: (messages) => set({ messages }),
  updateMessages: (updater) =>
    set((state) => ({ messages: updater(state.messages) })),
  requestScroll: (force = false) =>
    set((state) => ({
      scrollRequest: state.scrollRequest + 1,
      forceScrollRequest: force
        ? state.forceScrollRequest + 1
        : state.forceScrollRequest,
    })),
  resetActionButtons: () =>
    set((state) => ({ actionReset: state.actionReset + 1 })),
}));
