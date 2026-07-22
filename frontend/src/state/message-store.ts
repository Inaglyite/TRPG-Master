import { create } from "zustand";

export type VisualDie = {
  min: number;
  max: number;
  final: number;
  label: string;
  formatter?: "tens";
};

export type SpeakerAvatar = {
  asset_url?: string;
  asset_data_uri?: string;
  alt?: string;
};

export type Speaker = {
  type: "keeper" | "npc" | "investigator" | "system";
  id?: string;
  name: string;
  avatar?: SpeakerAvatar;
};

export type NarrativeSegment = {
  kind: "narration" | "speech";
  text: string;
  eventId?: string;
  /** wire-format compatibility */
  event_id?: string;
  /** 流式归因键；人物资料可晚于首段文本到达，再据此回填。 */
  npcId?: string;
  /** wire-format compatibility */
  npc_id?: string;
  speaker?: Speaker;
};

/** 服务端权威的公开聊天事件；NarrativeSegment 是旧协议的兼容名称。 */
export type ChatEvent = NarrativeSegment;

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
  /** 发言者段结构（守秘人旁白 + NPC 发言单元）；旧消息无此字段按原文渲染 */
  segments?: NarrativeSegment[];
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
