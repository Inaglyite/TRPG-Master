/**
 * structured-editor-store.ts — 结构化「出示 / 使用」编辑器草稿。
 *
 * 与旧的 `investigator-panel-store.editor` 并列但独立：旧编辑器编译成自然语言，
 * 这个编辑器编译成 `ActionRequest`。两者互不影响，legacy 世界继续走旧路径。
 *
 * 草稿保留：提交失败（协议不可用、目标失效、revision 冲突）时草稿不丢，
 * 只设置 error 供用户修改后重试。
 */

import { create } from "zustand";

import type { ActionTarget, PresentationKind } from "../protocol/structured";

export type StructuredEditorKind = "present" | "use";

export type StructuredEditorDraft = {
  kind: StructuredEditorKind;
  /** 面板块的展示名（线索摘要 / 道具名）。 */
  subject: string;
  clueId: string;
  /** 线索当前允许的出示方式（服务端投影）。 */
  presentations: PresentationKind[];
  /** 允许作为「展示原件」的关联物品 ID；空数组表示没有可用原件。 */
  allowedPhysicalItemIds: string[];
  itemId: string;
  /** 可用的常见用法；空表示只能自定义做法。 */
  operations: string[];
  availableQuantity: number;
  /** 世界标识：切换世界后草稿失效，不允许跨世界提交。 */
  worldId: string | null;
  // 用户填写字段
  presentation: PresentationKind;
  physicalItemId: string;
  targetKind: "" | "npc" | "investigator" | "scene_object" | "unresolved";
  targetId: string;
  targetText: string;
  question: string;
  quantity: number;
  operation: string;
  approach: string;
};

export type StructuredEditorState = {
  draft: StructuredEditorDraft | null;
  sending: boolean;
  error: string | null;
};

type StructuredEditorActions = {
  openPresent: (input: {
    clueId: string;
    subject: string;
    presentations: PresentationKind[];
    allowedPhysicalItemIds?: string[];
    worldId: string | null;
  }) => void;
  openUse: (input: {
    itemId: string;
    subject: string;
    operations: string[];
    availableQuantity: number;
    worldId: string | null;
  }) => void;
  update: (patch: Partial<StructuredEditorDraft>) => void;
  setError: (error: string | null) => void;
  setSending: (sending: boolean) => void;
  close: () => void;
  reset: () => void;
};

const INITIAL: StructuredEditorState = {
  draft: null,
  sending: false,
  error: null,
};

export const useStructuredEditorStore = create<
  StructuredEditorState & StructuredEditorActions
>((set) => ({
  ...INITIAL,

  openPresent: ({
    clueId,
    subject,
    presentations,
    allowedPhysicalItemIds,
    worldId,
  }) =>
    set({
      draft: {
        kind: "present",
        subject,
        clueId,
        presentations: presentations.length ? presentations : ["describe"],
        allowedPhysicalItemIds: allowedPhysicalItemIds ?? [],
        itemId: "",
        operations: [],
        availableQuantity: 0,
        worldId,
        // 默认最保守的方式：说明内容，不出示原件、不转移所有权。
        presentation: presentations.includes("describe")
          ? "describe"
          : (presentations[0] ?? "describe"),
        physicalItemId: "",
        targetKind: "",
        targetId: "",
        targetText: "",
        question: "",
        quantity: 1,
        operation: "",
        approach: "",
      },
      sending: false,
      error: null,
    }),

  openUse: ({ itemId, subject, operations, availableQuantity, worldId }) =>
    set({
      draft: {
        kind: "use",
        subject,
        clueId: "",
        presentations: [],
        allowedPhysicalItemIds: [],
        itemId,
        operations,
        availableQuantity,
        worldId,
        presentation: "describe",
        physicalItemId: "",
        targetKind: "",
        targetId: "",
        targetText: "",
        question: "",
        quantity: 1,
        operation: operations[0] ?? "",
        approach: "",
      },
      sending: false,
      error: null,
    }),

  update: (patch) =>
    set((state) =>
      state.draft
        ? { draft: { ...state.draft, ...patch }, error: null }
        : state,
    ),

  setError: (error) => set({ error }),
  setSending: (sending) => set({ sending }),
  close: () => set({ ...INITIAL }),
  reset: () => set({ ...INITIAL }),
}));

/** 目标是否已明确：选了候选 ID，或填了“描述其他对象”。 */
export function targetOf(draft: StructuredEditorDraft): ActionTarget | null {
  if (draft.targetKind === "unresolved") {
    const text = draft.targetText.trim();
    return text ? { kind: "unresolved", text } : null;
  }
  if (
    (draft.targetKind === "npc" ||
      draft.targetKind === "investigator" ||
      draft.targetKind === "scene_object") &&
    draft.targetId
  ) {
    return { kind: draft.targetKind, id: draft.targetId };
  }
  return null;
}

/** 出示方式的禁用原因；null 表示可用。用于按钮的 title/禁用提示。 */
export function presentationBlockReason(
  draft: StructuredEditorDraft,
  presentation: PresentationKind,
): string | null {
  if (presentation === "describe") return null;
  if (!draft.presentations.includes(presentation)) {
    return presentation === "image"
      ? "这条线索没有你可以出示的图片素材。"
      : "这条线索还没有可出示的关联实物。";
  }
  return null;
}

export type DraftValidation = { ok: true } | { ok: false; reason: string };

export function validateStructuredDraft(
  draft: StructuredEditorDraft,
): DraftValidation {
  if (draft.kind === "present") {
    if (!draft.clueId) return { ok: false, reason: "请选择要出示的线索。" };
    const blocked = presentationBlockReason(draft, draft.presentation);
    if (blocked) return { ok: false, reason: blocked };
    if (draft.presentation === "original" && !draft.physicalItemId) {
      return { ok: false, reason: "展示原件需要选择你实际持有的一件物品。" };
    }
    if (
      draft.presentation === "original" &&
      draft.allowedPhysicalItemIds.length === 0
    ) {
      return { ok: false, reason: "当前没有可作为原件的关联物品。" };
    }
    if (!targetOf(draft))
      return { ok: false, reason: "请选择目标，或描述其他对象。" };
    if (draft.targetKind === "unresolved" && !draft.targetText.trim()) {
      return { ok: false, reason: "请描述要向谁出示。" };
    }
    return { ok: true };
  }

  if (!draft.itemId) return { ok: false, reason: "请选择要使用的道具。" };
  if (!Number.isInteger(draft.quantity) || draft.quantity < 1) {
    return { ok: false, reason: "数量需要是至少 1 的整数。" };
  }
  if (draft.availableQuantity > 0 && draft.quantity > draft.availableQuantity) {
    return {
      ok: false,
      reason: `最多只能选择 ${draft.availableQuantity} 个。`,
    };
  }
  if (!draft.operation && !draft.approach.trim()) {
    return { ok: false, reason: "请选择常见用法，或写明你的做法。" };
  }
  return { ok: true };
}
