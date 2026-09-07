/**
 * investigator-panel-store.ts — 调查员侧栏三卡片的纯 UI 状态。
 *
 * 只存折叠/筛选/展开等展示偏好和出示/使用编辑器草稿；
 * 不存权威库存、线索或角色数据的副本（那些以 app-store 为准）。
 * 偏好按世界/时间线作用域（activeWorldId）；切世界时关闭编辑器、
 * 丢弃草稿，并清掉已不存在条目的展开状态。
 */

import { create } from "zustand";

import type { PanelActionDraft } from "../investigator-actions";
import { useAppStore } from "./app-store";

export type PanelCardId = "status" | "clues" | "items";
export type ClueFilter = "all" | "investigation" | "event" | "task" | "npc";

export type EditorState = {
  draft: PanelActionDraft;
  /** 打开编辑器时的世界，提交前必须仍为此世界。 */
  worldId: string | null;
  /** 提交阶段的瞬时锁：连点/Enter 只发送一次。 */
  sending: boolean;
  /** 最近一次校验/拒绝原因（展示在编辑器内）。 */
  error: string | null;
};

type WorldPrefs = {
  collapsed: Record<PanelCardId, boolean>;
  attributesOpen: boolean;
  clueFilter: ClueFilter;
  /** “全部”视图各分类的展开覆盖；缺省仅首个非空分类展开。 */
  groupOverrides: Record<string, boolean>;
  /** 展开了详情的线索键。 */
  expandedClues: string[];
  /** 已见过的线索键基线；之外的标记为“新增”。 */
  seenClueKeys: string[];
  seenInitialized: boolean;
};

const FALLBACK_WORLD = "__session__";

function defaultPrefs(): WorldPrefs {
  return {
    collapsed: { status: false, clues: false, items: false },
    attributesOpen: false,
    clueFilter: "all",
    groupOverrides: {},
    expandedClues: [],
    seenClueKeys: [],
    seenInitialized: false,
  };
}

// selector 缺省必须保持同一引用，避免每次 store 变化都触发重渲染。
const DEFAULT_PREFS = defaultPrefs();

type InvestigatorPanelState = {
  worldId: string | null;
  prefsByWorld: Record<string, WorldPrefs>;
  editor: EditorState | null;
  /** 世界切换同步：关闭编辑器/草稿；返回当前世界的偏好键。 */
  syncWorld: (worldId: string | null) => void;
  toggleCard: (card: PanelCardId) => void;
  setAttributesOpen: (open: boolean) => void;
  setClueFilter: (filter: ClueFilter) => void;
  setGroupOpen: (category: string, open: boolean) => void;
  toggleClueDetail: (clueKey: string) => void;
  /** 线索集合对齐：裁剪不存在的展开项；初始化“新增”基线。 */
  reconcileClues: (keys: string[]) => void;
  openEditor: (draft: PanelActionDraft) => void;
  closeEditor: () => void;
  updateEditorDraft: (patch: Partial<PanelActionDraft>) => void;
  setEditorError: (error: string | null) => void;
  setEditorSending: (sending: boolean) => void;
};

function worldKey(worldId: string | null): string {
  return worldId || FALLBACK_WORLD;
}

export const useInvestigatorPanelStore = create<InvestigatorPanelState>(
  (set, get) => {
    function patchPrefs(
      worldId: string | null,
      patch: (prefs: WorldPrefs) => Partial<WorldPrefs>,
    ): void {
      const key = worldKey(worldId);
      const current = get().prefsByWorld[key] || defaultPrefs();
      set((state) => ({
        prefsByWorld: {
          ...state.prefsByWorld,
          [key]: { ...current, ...patch(current) },
        },
      }));
    }

    return {
      worldId: null,
      prefsByWorld: {},
      editor: null,

      syncWorld: (worldId) => {
        const state = get();
        if (state.worldId === worldId) return;
        set({ worldId, editor: null });
        const key = worldKey(worldId);
        if (!state.prefsByWorld[key]) {
          set((current) => ({
            prefsByWorld: { ...current.prefsByWorld, [key]: defaultPrefs() },
          }));
        }
      },

      toggleCard: (card) => {
        const worldId = get().worldId;
        patchPrefs(worldId, (prefs) => ({
          collapsed: { ...prefs.collapsed, [card]: !prefs.collapsed[card] },
        }));
      },

      setAttributesOpen: (open) => {
        patchPrefs(get().worldId, () => ({ attributesOpen: open }));
      },

      setClueFilter: (filter) => {
        patchPrefs(get().worldId, () => ({ clueFilter: filter }));
      },

      setGroupOpen: (category, open) => {
        patchPrefs(get().worldId, (prefs) => ({
          groupOverrides: { ...prefs.groupOverrides, [category]: open },
        }));
      },

      toggleClueDetail: (clueKey) => {
        patchPrefs(get().worldId, (prefs) => {
          const expandedClues = prefs.expandedClues.includes(clueKey)
            ? prefs.expandedClues.filter((key) => key !== clueKey)
            : [...prefs.expandedClues, clueKey];
          // 查看详情即视为已读，摘掉“新增”标记。
          const seenClueKeys = prefs.seenClueKeys.includes(clueKey)
            ? prefs.seenClueKeys
            : [...prefs.seenClueKeys, clueKey];
          return { expandedClues, seenClueKeys };
        });
      },

      reconcileClues: (keys) => {
        patchPrefs(get().worldId, (prefs) => {
          const present = new Set(keys);
          const expandedClues = prefs.expandedClues.filter((key) =>
            present.has(key),
          );
          if (!prefs.seenInitialized) {
            // 首次见到本世界的线索集合：全部作为基线，不全部标“新增”。
            return {
              expandedClues,
              seenClueKeys: keys,
              seenInitialized: true,
            };
          }
          const seenClueKeys = prefs.seenClueKeys.filter((key) =>
            present.has(key),
          );
          return { expandedClues, seenClueKeys };
        });
      },

      openEditor: (draft) => {
        // 窄屏抽屉与编辑器遮罩不能叠加：打开编辑器时先收起侧栏抽屉。
        if (
          typeof window !== "undefined" &&
          typeof window.matchMedia === "function" &&
          window.matchMedia("(max-width: 999px)").matches
        ) {
          useAppStore.getState().setCharacterPanelOpen(false);
        }
        set({
          editor: {
            draft,
            worldId: get().worldId,
            sending: false,
            error: null,
          },
        });
      },

      closeEditor: () => set({ editor: null }),

      updateEditorDraft: (patch) => {
        const editor = get().editor;
        if (!editor) return;
        set({
          editor: {
            ...editor,
            error: null,
            draft: { ...editor.draft, ...patch } as PanelActionDraft,
          },
        });
      },

      setEditorError: (error) => {
        const editor = get().editor;
        if (!editor) return;
        set({ editor: { ...editor, error } });
      },

      setEditorSending: (sending) => {
        const editor = get().editor;
        if (!editor) return;
        set({ editor: { ...editor, sending } });
      },
    };
  },
);

/** 当前世界的展示偏好（组件订阅用）。 */
export function useWorldPrefs(): WorldPrefs {
  return useInvestigatorPanelStore(
    (state) => state.prefsByWorld[worldKey(state.worldId)] || DEFAULT_PREFS,
  );
}
