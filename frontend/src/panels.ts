/** React state adapters for character, handout, save and world panels. */

import { enableInput } from "./options";
import { addMsg, removeLoading } from "./renderer";
import {
  useAppStore,
  type AdventureEntry,
  type ClueItem,
  type Handout,
  type SaveEntry,
  type WorldEntry,
} from "./state/app-store";
import {
  isRoomOwner,
  timelineCapabilities,
  useOnlineStore,
} from "./state/online-store";
import { useStartStore } from "./state/start-store";
import { escapeHtml } from "./text";
import { getGameStarted } from "./start";
import { safeSend } from "./ws";

const clueCategories = ["investigation", "event", "task", "npc"] as const;
let knownClueKeys: Set<string> | null = null;
let handoutCounter = 0;
let quickSavePending = false;
let quickSaveTimeout: number | undefined;
let quickSaveFeedbackTimeout: number | undefined;
let clueToastTimeout: number | undefined;

/**
 * 多人房间的房主专属操作（存档管理、读档、结案等）前端门禁；
 * 服务端按 Session 再次校验，此处只是提前阻断并给出可读提示。
 */
function roomOwnerOpsAllowed(): boolean {
  if (useAppStore.getState().mode !== "online") return true;
  return isRoomOwner();
}

function denyRoomOwnerOp(): void {
  addMsg("system", "多人房间中，存档与结案操作仅房主可用。");
}

function parseJson<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function updateCharPanel(raw: string) {
  const data = parseJson<any>(raw, null);
  if (data) useAppStore.getState().setCharacter(data);
}

export function updateCluePanel(raw: string) {
  const clues = parseJson<Record<string, ClueItem[]>>(raw, {});
  const current = collectClueKeys(clues);
  if (knownClueKeys) {
    let added = 0;
    current.forEach((key) => {
      if (!knownClueKeys?.has(key)) added++;
    });
    if (added) {
      window.clearTimeout(clueToastTimeout);
      useAppStore.setState({
        clueToast: added > 1 ? `${added} 条线索已加入` : "线索已加入",
      });
      clueToastTimeout = window.setTimeout(
        () => useAppStore.setState({ clueToast: null }),
        2700,
      );
    }
  }
  knownClueKeys = current;
  useAppStore.getState().setClues(clues);
}

function collectClueKeys(clues: Record<string, ClueItem[]>) {
  const keys = new Set<string>();
  if (!clues || typeof clues !== "object" || Array.isArray(clues)) return keys;
  clueCategories.forEach((category) => {
    (clues[category] || []).forEach((item, index) => {
      if (item.type === "profile") return;
      keys.add(`${category}:${item.id || item.text || index}`);
    });
  });
  return keys;
}

export function loadState() {
  safeSend(JSON.stringify({ type: "state" }));
}

export function showHandout(data: Omit<Handout, "id">) {
  const source = data.asset_data_uri || data.asset_url;
  if (!source) return;
  const id = `${data.entity_type || "asset"}:${data.entity_id || data.file}:${++handoutCounter}`;
  useAppStore.getState().addHandout({ ...data, id });
}

export function clearTransientHandouts() {
  window.clearTimeout(clueToastTimeout);
  knownClueKeys = null;
  useAppStore.setState({ handouts: [], clueToast: null });
}

export function showEnding(data: any) {
  removeLoading();
  useAppStore.getState().setChoices([]);
  useAppStore.getState().setEnding(data);
}

export function confirmEnding(data: any) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  const emoji =
    data.ending_type === "good"
      ? "🏆"
      : data.ending_type === "bad"
        ? "💀"
        : "🌫";
  safeSend(
    JSON.stringify({
      type: "settle_case",
      ending_type: data.ending_type,
      title: data.title,
      summary: data.summary,
    }),
  );
  addMsg(
    "ending",
    [
      '<div class="ending-box">',
      `<div class="ending-emoji">${emoji}</div>`,
      `<div class="ending-title">${escapeHtml(data.title)}</div>`,
      `<div class="ending-summary">${escapeHtml(data.summary)}</div>`,
      "</div>",
    ].join(""),
  );
  useAppStore.getState().setEnding(null);
  enableInput(false);
}

export function openSavePanel(mode: "load" | "manage" = "manage") {
  useAppStore.getState().setSavePanel(true, mode);
  safeSend(JSON.stringify({ type: "save_list" }));
  if (useAppStore.getState().mode === "online") {
    // 云端单人房间支持时间线树查询（回复与本地同形的 world_list/adventure_list）；
    // 多人房间的旧协议没有该处理器，发送会收到 protocol_error 红条。
    if (timelineCapabilities().canList) {
      safeSend(JSON.stringify({ type: "solo_world_list" }));
    }
    return;
  }
  safeSend(JSON.stringify({ type: "world_list" }));
}

export function closeSavePanel() {
  useAppStore.getState().setSavePanel(false);
}

export function renderSavePanel(saves: SaveEntry[]) {
  useAppStore.getState().setSaveData(Array.isArray(saves) ? saves : []);
}

export function renderWorldPanel(worlds: WorldEntry[], activeWorldId: string) {
  useAppStore
    .getState()
    .setWorldData(Array.isArray(worlds) ? worlds : [], activeWorldId || "");
}

export function renderAdventurePanel(
  adventures: AdventureEntry[],
  activeWorldId: string,
) {
  const list = Array.isArray(adventures) ? adventures : [];
  useAppStore.getState().setAdventureData(list, activeWorldId || "");
  // 存档位列表是“从存档开始”可用性的权威来源：只要任意存档位存在，
  // 开局页 Load 入口就可用（不再只看当前时间线树的槽位）。
  useStartStore.setState({ hasSaves: list.length > 0 });
  // 云端单人“管理时间线”入口意图：adventure_list 只含当前存档位一个条目，
  // 落地后直接定位到该存档位的时间线视图。
  if (
    useAppStore.getState().mode === "online" &&
    useOnlineStore.getState().pendingTimelinePanel
  ) {
    useOnlineStore.setState({ pendingTimelinePanel: false });
    const rootId = String(list[0]?.root_world_id || "");
    if (list.length === 1 && rootId) {
      useAppStore.setState({
        savePanelView: { name: "timelines", rootId },
      });
    }
  }
}

/** 从存档卡片"继续游戏"：跨存档时先切到其最近可玩时间线。 */
let resumeAfterSwitch = false;

/** 继续某条时间线：当前时间线直接回到游戏；其他时间线先切换。 */
export function resumeTimeline(worldId: string, active: boolean) {
  const target = String(worldId || "");
  if (!target) return;
  const online = useAppStore.getState().mode === "online";
  if (active) {
    // 当前时间线：直接回到游戏。云端重连后服务端已恢复到最新状态，
    // 本地才需要在开局页补读 slot_000 自动存档。
    closeSavePanel();
    if (!online && !getGameStarted()) loadSave("slot_000");
    return;
  }
  // 云端切换 = 提交指针 + 断开重连，重连后服务端自动恢复最新状态；
  // 本地那套 resumeAfterSwitch/slot_000 逻辑不适用于房间协议。
  if (!online) resumeAfterSwitch = true;
  switchWorld(target);
}

export function resumeAdventure(adventure: AdventureEntry) {
  resumeTimeline(
    String(adventure.resume_world_id || ""),
    Boolean(adventure.active),
  );
}

/** world_switched 后若仍在开局页，立即读取自动存档完成"继续游戏"。 */
export function consumeResumeAfterSwitch(): boolean {
  const pending = resumeAfterSwitch;
  resumeAfterSwitch = false;
  return pending;
}

/** 从当前进度的最近完成回合创建时间线分支。 */
export function createBranchFromCurrentTurn(label: string) {
  const turnId = useAppStore.getState().latestBranchTurnId;
  if (!turnId) return;
  if (useAppStore.getState().mode === "online") {
    if (!timelineCapabilities().canCreateBranch) return;
    safeSend(
      JSON.stringify({
        type: "solo_branch_create",
        turn_id: turnId,
        label: label.trim(),
      }),
    );
    return;
  }
  safeSend(
    JSON.stringify({
      type: "turn_branch_create",
      turn_id: turnId,
      label: label.trim(),
    }),
  );
}

/** 重命名一条时间线的显示名。本地全允许；云端仅 solo 房间房主。 */
export function renameWorld(worldId: string, label: string) {
  const id = String(worldId || "").trim();
  if (!id) return;
  if (useAppStore.getState().mode === "online") {
    if (!timelineCapabilities().canRename) return;
    safeSend(
      JSON.stringify({
        type: "solo_world_rename",
        world_id: id,
        label: label.trim(),
      }),
    );
    return;
  }
  if (useAppStore.getState().mode !== "local") return;
  safeSend(
    JSON.stringify({ type: "world_rename", world_id: id, label: label.trim() }),
  );
}

export function loadSave(slotId: string) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  closeSavePanel();
  addMsg("system", "正在读档…");
  safeSend(JSON.stringify({ type: "save_load", slot_id: slotId }));
}

export function deleteSave(slotId: string) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  safeSend(JSON.stringify({ type: "save_delete", slot_id: slotId }));
}

export function renameSave(slotId: string, label: string) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  safeSend(
    JSON.stringify({
      type: "save_rename",
      slot_id: slotId,
      label: label.trim(),
    }),
  );
}

export function createSave() {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  safeSend(JSON.stringify({ type: "save_create" }));
  addMsg("system", "正在保存…");
}

export function switchWorld(worldId: string) {
  if (useAppStore.getState().mode === "online") {
    if (!timelineCapabilities().canSwitch) return;
    safeSend(JSON.stringify({ type: "solo_world_switch", world_id: worldId }));
    return;
  }
  safeSend(JSON.stringify({ type: "world_switch", world_id: worldId }));
}

/**
 * 删除一条已离开的本地时间线分支。
 *
 * UI 只会为非当前的分支展示入口；服务端仍会重新校验分支关系、活动状态
 * 与当前世界，不能把这个客户端检查当作权限边界。云端走 solo_world_archive
 * （仅 solo 房间房主，不能删除当前/主根时间线）。
 */
export function archiveWorld(worldId: string) {
  const id = String(worldId || "").trim();
  if (!id) return;
  if (useAppStore.getState().mode === "online") {
    if (!timelineCapabilities().canArchive) return;
    safeSend(JSON.stringify({ type: "solo_world_archive", world_id: id }));
    return;
  }
  if (useAppStore.getState().mode !== "local") return;
  safeSend(JSON.stringify({ type: "world_archive", world_id: id }));
}

/**
 * 删除整个存档位（主时间线 + 全部分支）。逻辑归档可恢复；
 * 当前正在游玩的存档位不可删除（服务端会再次校验）。仅本地模式。
 */
export function archiveAdventure(rootWorldId: string) {
  const id = String(rootWorldId || "").trim();
  if (!id || useAppStore.getState().mode !== "local") return;
  safeSend(JSON.stringify({ type: "adventure_archive", root_world_id: id }));
}

/** 重命名存档位的自定义显示名（仅元数据）。仅本地模式。 */
export function renameAdventure(rootWorldId: string, label: string) {
  const id = String(rootWorldId || "").trim();
  if (!id || useAppStore.getState().mode !== "local") return;
  safeSend(
    JSON.stringify({
      type: "adventure_rename",
      root_world_id: id,
      label: label.trim(),
    }),
  );
}

export function quickSave() {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  if (quickSavePending || !getGameStarted()) return;
  window.clearTimeout(quickSaveFeedbackTimeout);
  quickSavePending = true;
  useAppStore.setState({ quickSaveState: "saving" });
  safeSend(JSON.stringify({ type: "save", manual: false }));
  quickSaveTimeout = window.setTimeout(() => finishQuickSave(false), 8000);
}

export function finishQuickSave(ok: boolean) {
  if (!quickSavePending) return;
  quickSavePending = false;
  window.clearTimeout(quickSaveTimeout);
  useAppStore.setState({ quickSaveState: ok ? "success" : "failed" });
  quickSaveFeedbackTimeout = window.setTimeout(
    () => useAppStore.setState({ quickSaveState: "idle" }),
    1600,
  );
}
