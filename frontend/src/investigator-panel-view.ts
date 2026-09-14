/**
 * investigator-panel-view.ts — 面板数据源适配。
 *
 * 两条路径共用同一套卡片渲染：
 * - `legacy`：旧的 `app-store.clues` / `character.inventory`（字符串标签）。
 * - `structured`：服务端结构化投影（稳定 ID、可用出示方式、可用操作、数量）。
 *
 * 由世界的 `execution_profile` 决定用哪一份，**不混用**：结构化模式下
 * 不允许拿文本标签冒充实体 ID，所以也不会把旧的字符串库存塞进结构请求。
 * 结构化模式下投影为空时，面板显示明确的“等待服务端投影”，而不是拿旧数据
 * 装作结构化数据可用。
 */

import { useAppStore, type ClueItem, type ClueState } from "./state/app-store";
import { interactionPath } from "./protocol/structured";
import { useStructuredStore } from "./state/structured-store";

export type PanelPath = "legacy" | "structured";

export type PanelItem = {
  /** 稳定 ID；legacy 路径下为 null（只有标签，不能用于结构请求）。 */
  id: string | null;
  label: string;
  /** 服务端给出的可用数量；legacy 下为 null（未知，不显示数量）。 */
  quantity: number | null;
  /** 服务端声明的常见用法；legacy 下为空。 */
  operations: string[];
  /** legacy 下同标签出现的次数，仅用于展示。 */
  legacyCount: number;
};

export type PanelClues = {
  path: PanelPath;
  clues: ClueState;
  /** 结构化模式下是否有服务端投影（用于空态提示）。 */
  ready: boolean;
};

export function currentPanelPath(): PanelPath {
  return interactionPath(useStructuredStore.getState().capabilities);
}

function structuredCluesToState(): ClueState {
  const state = useStructuredStore.getState();
  const result: ClueState = {};
  for (const clue of state.clues) {
    const category = clue.category || "investigation";
    const item: ClueItem = {
      id: clue.id,
      text: clue.text,
      type: "obvious",
      asset: clue.assetLabel ? { label: clue.assetLabel } : null,
    };
    const bucket = result[category] ?? [];
    bucket.push(item);
    result[category] = bucket;
  }
  return result;
}

export function usePanelClues(): PanelClues {
  const path = useStructuredStore((state) =>
    interactionPath(state.capabilities),
  );
  const structuredClues = useStructuredStore((state) => state.clues);
  const legacyClues = useAppStore((state) => state.clues);
  if (path === "structured") {
    return {
      path,
      clues: structuredCluesToState(),
      ready: structuredClues.length > 0,
    };
  }
  return { path, clues: legacyClues, ready: true };
}

export function usePanelItems(): { path: PanelPath; items: PanelItem[] } {
  const path = useStructuredStore((state) =>
    interactionPath(state.capabilities),
  );
  const structuredItems = useStructuredStore((state) => state.items);
  const legacyInventory = useAppStore((state) => state.character?.inventory);
  if (path === "structured") {
    return {
      path,
      items: structuredItems.map((item) => ({
        id: item.id,
        label: item.label,
        quantity: item.quantity,
        operations: item.operations,
        legacyCount: 1,
      })),
    };
  }
  // legacy：同标签合并显示，但明确不冒充后端堆叠数量。
  const counts = new Map<string, number>();
  for (const label of legacyInventory ?? []) {
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return {
    path,
    items: [...counts.entries()].map(([label, legacyCount]) => ({
      id: null,
      label,
      quantity: null,
      operations: [],
      legacyCount,
    })),
  };
}

/**
 * 结构化模式下某条线索的出示能力；legacy 下返回 null（走旧编辑器）。
 * 没有稳定 ID 的线索不能结构化出示——不编造 ID。
 */
export function currentStructuredClueOption(clueId: string | null | undefined) {
  if (!clueId) return null;
  return (
    useStructuredStore.getState().clues.find((clue) => clue.id === clueId) ??
    null
  );
}
