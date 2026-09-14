/**
 * investigator-structured-actions.ts — 面板「出示 / 使用」的双路径决策点。
 *
 * 只有一个地方决定走结构化还是旧文字通道：`execution_profile`。
 * - structured_v1：把草稿编译成 `ActionRequest`（ID + 枚举 + 数量），
 *   协议不可用时**明确报错**，绝不改走文字通道。
 * - legacy：保持旧行为（`buildPanelActionText` + `sendAction`）。
 */

import { sendStructuredAction } from "./structured-transport";
import {
  PRESENTATIONS,
  structuredUnavailableReason,
  type PresentationKind,
  type StructuredAction,
} from "./protocol/structured";
import { useAppStore } from "./state/app-store";
import {
  targetOf,
  useStructuredEditorStore,
  validateStructuredDraft,
  type StructuredEditorDraft,
} from "./state/structured-editor-store";
import { useStructuredStore } from "./state/structured-store";
import {
  currentPanelPath,
  currentStructuredClueOption,
} from "./investigator-panel-view";

export type SubmitResult = { ok: true } | { ok: false; reason: string };

/**
 * 结构化提交的前置条件（**不含**回合制的“守秘人正在叙述”）。：与旧编辑器同一时序约定（守秘人正在叙述时先别提交），
 * 但结构化路径把它做成**可见的禁用原因**，而不是让按钮看起来可点再报错。
 */
export function narrationGuardReason(): string | null {
  const app = useAppStore.getState();
  if (app.connection !== "connected") return "连接已断开，暂时无法提交。";
  if (app.dialog) return "请先完成当前的检定或决定。";
  if (app.ending) return "请先处理结局确认。";
  if (app.choices.some((choice) => choice.decisionId))
    return "请先完成当前的决定。";
  return null;
}

/** 结构化协议是否可用于提交；返回原因文本表示不可用。 */
export function structuredPathBlockReason(): string | null {
  const state = useStructuredStore.getState();
  return structuredUnavailableReason(state.capabilities, state.protocolNotice);
}

function asPresentations(values: string[]): PresentationKind[] {
  return values.filter((value): value is PresentationKind =>
    (PRESENTATIONS as readonly string[]).includes(value),
  );
}

/**
 * 点击「出示」：结构化模式打开结构化编辑器，否则打开旧编辑器。
 * 返回 false 表示没有可用的结构化线索投影（由调用方禁用按钮并说明原因）。
 */
export function beginPresentClue(input: {
  clueId: string | null;
  summary: string;
}): boolean {
  if (currentPanelPath() !== "structured") return false;
  const option = currentStructuredClueOption(input.clueId);
  if (!option) return false;
  useStructuredEditorStore.getState().openPresent({
    clueId: option.id,
    subject: input.summary || option.text.slice(0, 40) || option.id,
    presentations: asPresentations(option.presentation),
    allowedPhysicalItemIds: option.allowedPhysicalItemIds ?? [],
    worldId: useAppStore.getState().activeWorldId,
  });
  return true;
}

export function beginUseItem(input: {
  itemId: string | null;
  label: string;
  operations: string[];
  quantity: number | null;
}): boolean {
  if (currentPanelPath() !== "structured") return false;
  if (!input.itemId) return false;
  useStructuredEditorStore.getState().openUse({
    itemId: input.itemId,
    subject: input.label,
    operations: input.operations,
    availableQuantity: input.quantity ?? 0,
    worldId: useAppStore.getState().activeWorldId,
  });
  return true;
}

/** 结构化模式下为什么这条线索不能结构出示（用于按钮禁用提示）。 */
export function presentUnavailableReason(
  clueId: string | null | undefined,
): string | null {
  if (currentPanelPath() !== "structured") return null;
  const blocked = structuredPathBlockReason();
  if (blocked) return blocked;
  if (!clueId)
    return "该线索缺少服务端稳定 ID，已禁止结构化出示（不用文本冒充 ID）。";
  if (!currentStructuredClueOption(clueId)) {
    return "服务端尚未提供这条线索的公开投影，暂时无法结构化出示。";
  }
  return null;
}

export function useUnavailableReason(
  itemId: string | null | undefined,
): string | null {
  if (currentPanelPath() !== "structured") return null;
  const blocked = structuredPathBlockReason();
  if (blocked) return blocked;
  if (!itemId)
    return "该物品缺少服务端稳定 ID，已禁止结构化使用（不用标签冒充 ID）。";
  return null;
}

/** 草稿 → 结构化 action。文本只进 approach/question，不进权威字段。 */
export function compileStructuredAction(
  draft: StructuredEditorDraft,
): StructuredAction | { reason: string } {
  const validation = validateStructuredDraft(draft);
  if (!validation.ok) return { reason: validation.reason };
  const target = targetOf(draft);

  if (draft.kind === "present") {
    return {
      kind: "present_clue",
      clue_id: draft.clueId,
      presentation: draft.presentation,
      physical_item_id:
        draft.presentation === "original" ? draft.physicalItemId || null : null,
      // 目标缺失时也允许提交“描述其他对象”的未解析文本，由主持澄清。
      target: target ?? { kind: "unresolved", text: draft.targetText.trim() },
      ...(draft.question.trim() ? { question: draft.question.trim() } : {}),
    };
  }

  return {
    kind: "use_item",
    item_id: draft.itemId,
    quantity: draft.quantity,
    operation: draft.operation || "custom",
    ...(target ? { target } : {}),
    ...(draft.approach.trim() ? { approach: draft.approach.trim() } : {}),
  };
}

/**
 * 用已提交过的请求载荷重新打开编辑器（“重新编辑”）。
 *
 * 提交成功后编辑器会关闭，请求体仍留在 store 里；服务端随后拒绝（目标失效、
 * revision 冲突）时，玩家需要改目标/改做法而不是只能原样重试。这里把载荷映射
 * 回草稿，保证“错误后草稿仍可编辑”，而不是让用户从头再选一遍。
 */
export function reopenStructuredEditor(request: {
  kind: string;
  payload: unknown;
}): boolean {
  const payload = request.payload as
    { action?: Record<string, unknown> } | undefined;
  const action = payload?.action;
  if (!action || typeof action !== "object") return false;
  const kind = String(action.kind ?? "");

  if (kind === "present_clue") {
    const clueId = String(action.clue_id ?? "");
    const option = currentStructuredClueOption(clueId);
    const target = action.target as Record<string, unknown> | undefined;
    const targetKind = String(target?.kind ?? "");
    useStructuredEditorStore.getState().openPresent({
      clueId,
      subject: option?.text.slice(0, 40) || clueId,
      presentations: asPresentations(option?.presentation ?? ["describe"]),
      allowedPhysicalItemIds: option?.allowedPhysicalItemIds ?? [],
      worldId: useAppStore.getState().activeWorldId,
    });
    useStructuredEditorStore.getState().update({
      presentation: String(
        action.presentation ?? "describe",
      ) as PresentationKind,
      physicalItemId: String(action.physical_item_id ?? ""),
      targetKind:
        targetKind === "unresolved"
          ? "unresolved"
          : targetKind === "npc" ||
              targetKind === "investigator" ||
              targetKind === "scene_object"
            ? targetKind
            : "",
      targetId:
        targetKind === "unresolved"
          ? String(target?.text ?? "")
          : String(target?.id ?? ""),
      targetText: targetKind === "unresolved" ? String(target?.text ?? "") : "",
      question: String(action.question ?? ""),
    });
    return true;
  }

  if (kind === "use_item") {
    const itemId = String(action.item_id ?? "");
    const item = useStructuredStore
      .getState()
      .items.find((entry) => entry.id === itemId);
    const target = action.target as Record<string, unknown> | undefined;
    const targetKind = String(target?.kind ?? "");
    useStructuredEditorStore.getState().openUse({
      itemId,
      subject: item?.label ?? itemId,
      operations: item?.operations ?? [],
      availableQuantity: item?.quantity ?? 0,
      worldId: useAppStore.getState().activeWorldId,
    });
    useStructuredEditorStore.getState().update({
      quantity: Number(action.quantity ?? 1) || 1,
      operation: String(action.operation ?? ""),
      approach: String(action.approach ?? ""),
      targetKind:
        targetKind === "unresolved"
          ? "unresolved"
          : targetKind === "npc" ||
              targetKind === "investigator" ||
              targetKind === "scene_object"
            ? targetKind
            : "",
      targetId:
        targetKind === "unresolved"
          ? String(target?.text ?? "")
          : String(target?.id ?? ""),
      targetText: targetKind === "unresolved" ? String(target?.text ?? "") : "",
    });
    return true;
  }

  return false;
}

/** 提交结构化编辑器草稿。失败时保留草稿并设置 error。 */
export function submitStructuredEditor(): SubmitResult {
  const editor = useStructuredEditorStore.getState();
  const draft = editor.draft;
  if (!draft) return { ok: false, reason: "没有待提交的草稿。" };

  const blocked = structuredPathBlockReason();
  if (blocked) {
    editor.setError(blocked);
    return { ok: false, reason: blocked };
  }
  if (
    draft.worldId !== null &&
    useAppStore.getState().activeWorldId !== draft.worldId
  ) {
    const reason = "已切换世界/时间线，请重新发起行动。";
    editor.setError(reason);
    return { ok: false, reason };
  }

  const compiled = compileStructuredAction(draft);
  if ("reason" in compiled) {
    editor.setError(compiled.reason);
    return { ok: false, reason: compiled.reason };
  }

  editor.setSending(true);
  const result = sendStructuredAction(
    compiled,
    draft.kind === "present"
      ? `出示：${draft.subject}`
      : `使用：${draft.subject}`,
  );
  if (!result.ok) {
    editor.setSending(false);
    editor.setError(result.reason);
    return { ok: false, reason: result.reason };
  }
  editor.close();
  return { ok: true };
}
