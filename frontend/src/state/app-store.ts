import { create } from "zustand";

export const DEFAULT_TITLE = "TRPG Game";
export const DEFAULT_SUBTITLE = "A TRPG of Madness & Mystery";

export type ConnectionState = "connecting" | "connected" | "disconnected";

export type CharacterState = {
  name?: string;
  occupation?: string;
  hp?: number;
  max_hp?: number;
  san?: number;
  max_san?: number;
  attributes?: Record<string, unknown>;
  inventory?: string[];
  avatar?: {
    asset_url?: string;
    asset_data_uri?: string;
    alt?: string;
  };
};

export type ClueAsset = {
  id?: string;
  file?: string;
  label?: string;
  asset_data_uri?: string;
  asset_url?: string;
};

export type ClueItem = {
  id?: string;
  text?: string;
  type?: string;
  tier?: number;
  asset?: ClueAsset | null;
};

export type ClueState = Record<string, ClueItem[]>;
export type ActionChoice = { label: string; isFree: boolean };
export type SuggestDialog = {
  kind: "suggest";
  description?: string;
  skill?: string;
  attribute?: string;
  dc_label?: string;
  dc?: unknown;
};
export type DecisionOption = {
  id: string;
  label?: string;
  description?: string;
};
export type DecisionDialog = {
  kind: "decision";
  id: string;
  title?: string;
  description?: string;
  options: DecisionOption[];
};
export type InteractionDialog = SuggestDialog | DecisionDialog;
export type EndingProposal = {
  ending_type: string;
  title: string;
  summary: string;
};
export type Handout = {
  id: string;
  file: string;
  label: string;
  asset_data_uri: string;
  asset_url: string;
  entity_type: string;
  entity_id: string;
};
export type SaveEntry = {
  id: string;
  label?: string;
  scene_name?: string;
  character_name?: string;
  hp?: unknown;
  san?: unknown;
  clue_count?: number;
  message_count?: number;
  created_at?: string;
};
export type WorldEntry = {
  world_id?: string;
  active?: boolean;
  is_branch?: boolean;
  label?: string;
  scene_name?: string;
  character_name?: string;
};
export type QuickSaveState = "idle" | "saving" | "success" | "failed";

/** 应用运行模式：select=启动后待选择；local=单机本地后端；online=多人云端。 */
export type AppMode = "select" | "local" | "online";

/**
 * 通过 URL 标记直接进入指定模式（如 Electron 联机由主进程同源加载
 * `https://<cloud>/?mode=online`）；无标记时进入模式选择。
 */
export function detectInitialMode(): AppMode {
  try {
    const mode = new URLSearchParams(window.location.search).get("mode");
    if (mode === "online" || mode === "local") return mode;
  } catch {
    /* 非浏览器环境按 select 处理 */
  }
  return "select";
}

type AppState = {
  mode: AppMode;
  connection: ConnectionState;
  connectionNotice: string | null;
  connectionRecoveryAvailable: boolean;
  activeWorldId: string | null;
  activeModule: string | null;
  title: string;
  subtitle: string;
  description: string;
  startButtonText: string;
  character: CharacterState | null;
  clues: ClueState;
  inputEnabled: boolean;
  inputPlaceholder: string;
  choices: ActionChoice[];
  dialog: InteractionDialog | null;
  ending: EndingProposal | null;
  utilityOpen: boolean;
  notesText: string;
  notesRevision: number;
  notesDirty: boolean;
  notesSaving: boolean;
  notesLoading: boolean;
  notesStatus: string;
  notesStatusKind: string;
  characterPanelOpen: boolean;
  handouts: Handout[];
  clueToast: string | null;
  savePanelOpen: boolean;
  savePanelMode: "load" | "manage";
  saves: SaveEntry[];
  worlds: WorldEntry[];
  renameSlotId: string | null;
  quickSaveState: QuickSaveState;
  setMode: (mode: AppMode) => void;
  setConnection: (connection: ConnectionState) => void;
  setConnectionNotice: (
    message: string | null,
    recoveryAvailable?: boolean,
  ) => void;
  setWorld: (worldId: string | null, moduleName: string | null) => void;
  setTitle: (title: string) => void;
  setSubtitle: (subtitle: string) => void;
  setDescription: (description: string) => void;
  setStartButtonText: (text: string) => void;
  setCharacter: (character: CharacterState) => void;
  setClues: (clues: ClueState) => void;
  setInput: (enabled: boolean, placeholder?: string) => void;
  setChoices: (choices: ActionChoice[]) => void;
  setDialog: (dialog: InteractionDialog | null) => void;
  setEnding: (ending: EndingProposal | null) => void;
  setUtilityOpen: (open: boolean) => void;
  setNotesDraft: (text: string) => void;
  applyNotes: (payload: {
    text?: unknown;
    revision?: unknown;
    saved?: unknown;
  }) => void;
  setNotesProgress: (
    state: Partial<
      Pick<
        AppState,
        | "notesSaving"
        | "notesLoading"
        | "notesDirty"
        | "notesStatus"
        | "notesStatusKind"
      >
    >,
  ) => void;
  setCharacterPanelOpen: (open: boolean) => void;
  addHandout: (handout: Handout) => void;
  dismissHandout: (id: string) => void;
  setSavePanel: (open: boolean, mode?: "load" | "manage") => void;
  setSaveData: (saves: SaveEntry[]) => void;
  setWorldData: (worlds: WorldEntry[], activeWorldId: string) => void;
};

export const useAppStore = create<AppState>((set) => ({
  mode: detectInitialMode(),
  connection: "connecting",
  connectionNotice: null,
  connectionRecoveryAvailable: false,
  activeWorldId: null,
  activeModule: null,
  title: DEFAULT_TITLE,
  subtitle: DEFAULT_SUBTITLE,
  description: "",
  startButtonText: "",
  character: null,
  clues: {},
  inputEnabled: false,
  inputPlaceholder: "等待守秘人叙述……",
  choices: [],
  dialog: null,
  ending: null,
  utilityOpen: false,
  notesText: "",
  notesRevision: 0,
  notesDirty: false,
  notesSaving: false,
  notesLoading: false,
  notesStatus: "",
  notesStatusKind: "",
  characterPanelOpen: false,
  handouts: [],
  clueToast: null,
  savePanelOpen: false,
  savePanelMode: "manage",
  saves: [],
  worlds: [],
  renameSlotId: null,
  quickSaveState: "idle",
  setMode: (mode) => set({ mode }),
  setConnection: (connection) => set({ connection }),
  setConnectionNotice: (
    connectionNotice,
    connectionRecoveryAvailable = false,
  ) =>
    set({
      connectionNotice,
      connectionRecoveryAvailable:
        connectionNotice !== null && connectionRecoveryAvailable,
    }),
  setWorld: (activeWorldId, activeModule) =>
    set({ activeWorldId, activeModule }),
  setTitle: (title) => set({ title }),
  setSubtitle: (subtitle) => set({ subtitle }),
  setDescription: (description) => set({ description }),
  setStartButtonText: (startButtonText) => set({ startButtonText }),
  setCharacter: (character) => set({ character }),
  setClues: (clues) => set({ clues }),
  setInput: (inputEnabled, inputPlaceholder) =>
    set((state) => ({
      inputEnabled,
      inputPlaceholder:
        inputPlaceholder ??
        (inputEnabled ? "你决定做什么？" : state.inputPlaceholder),
    })),
  setChoices: (choices) => set({ choices }),
  setDialog: (dialog) => set({ dialog }),
  setEnding: (ending) => set({ ending }),
  setUtilityOpen: (utilityOpen) => set({ utilityOpen }),
  setNotesDraft: (notesText) =>
    set({
      notesText,
      notesDirty: true,
      notesStatus: "未保存",
      notesStatusKind: "",
    }),
  applyNotes: (payload) =>
    set((state) => ({
      notesText: state.notesDirty
        ? state.notesText
        : String(payload.text || ""),
      notesRevision: Number(payload.revision || 0),
      notesSaving: false,
      notesLoading: false,
      notesDirty: state.notesDirty,
      notesStatus: state.notesDirty ? "未保存" : payload.saved ? "已保存" : "",
      notesStatusKind: state.notesDirty ? "" : payload.saved ? "success" : "",
    })),
  setNotesProgress: (state) => set(state),
  setCharacterPanelOpen: (characterPanelOpen) => set({ characterPanelOpen }),
  addHandout: (handout) =>
    set((state) => ({ handouts: [...state.handouts, handout] })),
  dismissHandout: (id) =>
    set((state) => ({
      handouts: state.handouts.filter((handout) => handout.id !== id),
    })),
  setSavePanel: (savePanelOpen, savePanelMode) =>
    set((state) => ({
      savePanelOpen,
      savePanelMode: savePanelMode || state.savePanelMode,
      renameSlotId: null,
    })),
  setSaveData: (saves) => set({ saves }),
  setWorldData: (worlds, activeWorldId) => set({ worlds, activeWorldId }),
}));
