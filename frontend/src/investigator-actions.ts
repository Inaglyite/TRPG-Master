/**
 * investigator-actions.ts — 调查员面板「出示 / 使用」行动意图构造。
 *
 * 纯函数层：把编辑器草稿编译成一条普通玩家行动文本，经现有
 * {type:"action"} 回合入口提交。这里不产生任何状态修改，不绕过
 * 后端裁决；可测试、可独立演进。
 */

import { sendAction } from "./options";
import { useAppStore, type ClueState } from "./state/app-store";
import { canCurrentUserAct } from "./state/online-store";

export type PresentDraft = {
  kind: "present";
  /** 发起出示时的线索稳定键（提交前用于核对线索仍存在）。 */
  clueKey: string;
  /** 线索摘要（展示与行动文本共用，来自公开名称或正文首行）。 */
  clueSummary: string;
  /** 向谁出示/说明（必填，自由文本；不自动选唯一 NPC）。 */
  target: string;
  /** 想询问什么（可选）。 */
  question: string;
  /** 玩家明确选择的库存实物标签；为空表示仅“说明线索”。 */
  physicalItem: string | null;
};

export type UseDraft = {
  kind: "use";
  /** 库存原始标签（权威库存是字符串数组）。 */
  itemLabel: string;
  /** 如何使用（必填）。 */
  usage: string;
  /** 目标/对象（可选）。 */
  target: string;
};

export type PanelActionDraft = PresentDraft | UseDraft;

/** 线索摘要：优先公共素材标签，否则取公开正文首行；纯展示，不编造事实。 */
export function clueSummaryOf(clue: {
  text?: string;
  asset?: { label?: string } | null;
}): string {
  const label = clue.asset?.label?.trim();
  if (label) return label;
  const firstLine = (clue.text || "").split("\n")[0]?.trim() || "";
  if (!firstLine) return "未命名线索";
  return firstLine.length > 40 ? `${firstLine.slice(0, 40)}…` : firstLine;
}

/** 出示预览：默认是“说明线索”，不产生实物转移；选实物时只是出示查看。 */
export function buildPresentText(draft: PresentDraft): string {
  const target = draft.target.trim();
  const question = draft.question.trim();
  if (draft.physicalItem) {
    const base = `我向${target}出示随身携带的「${draft.physicalItem}」，让他查看，不递交、不赠送、不消耗。`;
    return question ? `${base}同时询问：${question}。` : base;
  }
  const base = `我向${target}说明我已知的线索：「${draft.clueSummary}」`;
  return question
    ? `${base}，并询问：${question}。`
    : `${base}，询问他对此的看法。`;
}

/** 使用预览：只表达尝试意图，是否消耗/检定/成功由后端规则结算。 */
export function buildUseText(draft: UseDraft): string {
  const usage = draft.usage.trim();
  const target = draft.target.trim();
  if (target) return `我尝试用「${draft.itemLabel}」对${target}${usage}。`;
  return `我尝试用「${draft.itemLabel}」${usage}。`;
}

export function buildPanelActionText(draft: PanelActionDraft): string {
  return draft.kind === "present"
    ? buildPresentText(draft)
    : buildUseText(draft);
}

/** 草稿内容校验（与权限无关）；返回错误文案，null 表示可提交。 */
export function validatePanelDraft(draft: PanelActionDraft): string | null {
  if (draft.kind === "present") {
    if (!draft.target.trim()) return "请填写要向谁出示/说明。";
    return null;
  }
  if (!draft.usage.trim()) return "请描述想怎么使用。";
  return null;
}

/**
 * 回合入口门禁的只读摘要：与发送框/快捷行动同一套约束
 * （连接、输入开放、无待处理弹窗或结局、在线轮到本人）。
 * 这里只是体验提示；服务端权限与裁决仍是最终边界。
 */
export function panelActionBlockReason(): string | null {
  const app = useAppStore.getState();
  if (app.connection !== "connected") return "连接已断开，暂时无法行动。";
  if (app.dialog) return "请先完成当前的检定或决定。";
  if (app.ending) return "请先处理结局确认。";
  if (app.choices.some((choice) => choice.decisionId))
    return "请先完成当前的决定。";
  if (!app.inputEnabled) return "守秘人正在叙述，稍后再行动。";
  if (app.mode === "online" && !canCurrentUserAct())
    return "还没有轮到你行动。";
  return null;
}

/** 提交前的过期草稿核对：世界/线索/物品必须与打开编辑器时一致。 */
export function staleDraftReason(
  draft: PanelActionDraft,
  worldId: string | null,
  clues: ClueState,
  inventory: string[],
): string | null {
  if (draft.kind === "present") {
    const stillThere = Object.entries(clues).some(([category, items]) =>
      (items || []).some(
        (item, index) =>
          `${category}:${item.id || item.text || index}` === draft.clueKey,
      ),
    );
    if (!stillThere) return "这条线索已不在当前档案中，请重新选择。";
    if (draft.physicalItem && !inventory.includes(draft.physicalItem))
      return `「${draft.physicalItem}」已不在随身物品中。`;
    return null;
  }
  if (!inventory.includes(draft.itemLabel))
    return `「${draft.itemLabel}」已不在随身物品中。`;
  return null;
}

export type PanelSubmitResult = { ok: true } | { ok: false; reason: string };

/**
 * 共享提交适配：校验 → 门禁 → 过期核对 → sendAction。
 * sendAction 返回“已接受发送”；拒绝时保留草稿由调用方提示。
 */
export function submitPanelAction(
  draft: PanelActionDraft,
  worldId: string | null,
): PanelSubmitResult {
  const invalid = validatePanelDraft(draft);
  if (invalid) return { ok: false, reason: invalid };
  const app = useAppStore.getState();
  if (app.activeWorldId !== worldId)
    return { ok: false, reason: "已切换世界/时间线，请重新发起行动。" };
  const blocked = panelActionBlockReason();
  if (blocked) return { ok: false, reason: blocked };
  const stale = staleDraftReason(
    draft,
    worldId,
    app.clues,
    app.character?.inventory || [],
  );
  if (stale) return { ok: false, reason: stale };
  const accepted = sendAction(buildPanelActionText(draft));
  if (!accepted) return { ok: false, reason: "上一项行动仍在处理中，请稍候。" };
  return { ok: true };
}
