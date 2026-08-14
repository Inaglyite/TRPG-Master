import { useEffect, useState } from "react";

import * as panels from "../../panels";
import {
  useAppStore,
  type Handout,
  type SaveEntry,
} from "../../state/app-store";
import type { TimelineEntry } from "../../state/app-store";
import { CharacterPanelContent } from "./CharacterPanelContent";
import { useDelayedClose, usePhaseTransition } from "./transitions";

function panelCommand(name: string, ...args: unknown[]) {
  const command = (panels as Record<string, (...values: any[]) => void>)[name];
  command?.(...args);
}

export function CharacterPanel() {
  const open = useAppStore((state) => state.characterPanelOpen);
  return (
    <aside id="char-panel" className={open ? "" : "collapsed"}>
      <div id="char-content">
        <CharacterPanelContent />
      </div>
    </aside>
  );
}

function HandoutCard({ handout }: { handout: Handout }) {
  const dismiss = useAppStore((state) => state.dismissHandout);
  const [expanded, setExpanded] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const close = () => {
    if (leaving) return;
    setLeaving(true);
    window.setTimeout(() => dismiss(handout.id), 280);
  };
  useEffect(() => {
    const timer = window.setTimeout(close, 10000);
    return () => window.clearTimeout(timer);
  });
  const source = handout.asset_data_uri || handout.asset_url;
  return (
    <>
      <div className={`handout-card${leaving ? " leaving" : ""}`}>
        <div className="handout-header">
          <span className="handout-label">{handout.label || handout.file}</span>
          <button className="handout-close" onClick={close}>
            ✕
          </button>
        </div>
        <img
          src={source}
          alt={handout.label || handout.file}
          loading="lazy"
          onClick={() => setExpanded(true)}
        />
      </div>
      {expanded && (
        <div
          className="handout-overlay"
          role="presentation"
          onClick={() => setExpanded(false)}
        >
          <img src={source} alt={handout.label || handout.file} />
        </div>
      )}
    </>
  );
}

export function HandoutLayer() {
  const handouts = useAppStore((state) => state.handouts);
  const clueToast = useAppStore((state) => state.clueToast);
  return (
    <>
      <div id="handout-container">
        {handouts.map((handout) => (
          <HandoutCard key={handout.id} handout={handout} />
        ))}
      </div>
      {clueToast && (
        <div id="toast-stack">
          <div className="clue-toast">{clueToast}</div>
        </div>
      )}
    </>
  );
}

function formatSaveTime(createdAt?: string) {
  if (!createdAt) return { absolute: "未知时间", relative: "" };
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime()))
    return { absolute: "未知时间", relative: "" };
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  const relative =
    minutes < 1
      ? "刚刚"
      : minutes < 60
        ? `${minutes}分钟前`
        : minutes < 1440
          ? `${Math.floor(minutes / 60)}小时前`
          : `${Math.floor(minutes / 1440)}天前`;
  return {
    absolute: date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }),
    relative,
  };
}

function SaveRow({
  save,
  latest,
  selected,
  onSelect,
}: {
  save: SaveEntry;
  latest: boolean;
  selected: boolean;
  onSelect: (saveId: string) => void;
}) {
  const renameSlotId = useAppStore((state) => state.renameSlotId);
  const [name, setName] = useState(save.label || save.scene_name || "");
  const renaming = renameSlotId === save.id;
  const isAuto = save.id === "slot_000";
  const time = formatSaveTime(save.created_at);
  // 存档列表聚合整个分支树；属于其他时间线的存档只能先切换过去再读取，
  // 因为读取/重命名/删除协议都作用于当前时间线。
  const foreign = Boolean(save.world_id) && save.world_active === false;
  return (
    <div
      className={`save-slot-entry${latest ? " save-latest" : ""}${selected ? " save-selected" : ""}`}
      data-slot={save.id}
    >
      <div className="save-slot-info">
        <div className="save-slot-title">
          {renaming && !foreign ? (
            <div className="save-rename-form">
              <input
                autoFocus
                className="save-rename-input"
                maxLength={50}
                value={name}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter")
                    void panelCommand("renameSave", save.id, name);
                  if (event.key === "Escape")
                    useAppStore.setState({ renameSlotId: null });
                }}
              />
              <button
                className="save-rename-confirm"
                aria-label="确认重命名"
                data-tooltip="确认重命名"
                onClick={() => void panelCommand("renameSave", save.id, name)}
              >
                ✓
              </button>
              <button
                className="save-rename-cancel"
                aria-label="取消重命名"
                data-tooltip="取消重命名"
                onClick={() => useAppStore.setState({ renameSlotId: null })}
              >
                ×
              </button>
            </div>
          ) : (
            <span className="save-slot-name">
              {latest && (
                <>
                  <span className="save-badge">最新</span>{" "}
                </>
              )}
              {save.label || save.scene_name || "未知场景"}
            </span>
          )}
          <span className="save-slot-time">
            {time.relative} · {time.absolute}
          </span>
        </div>
        <div className="save-slot-meta">
          {save.timeline_label && (
            <button
              type="button"
              className="save-timeline-tag"
              aria-label={`查看「${save.timeline_label}」的时间线`}
              aria-pressed={selected}
              onClick={() => onSelect(save.id)}
            >
              {save.timeline_label}
            </button>
          )}
          <span>{save.scene_name || "未知场景"}</span>
          <span>{save.character_name || "未知调查员"}</span>
          <span>
            HP {String(save.hp ?? "?")} SAN {String(save.san ?? "?")}
          </span>
          <span>{save.clue_count ?? 0} 线索</span>
          <span>{save.message_count ?? 0} 消息</span>
        </div>
      </div>
      <div className="save-slot-actions">
        {foreign ? (
          <button
            className="save-action-switch"
            onClick={() => void panelCommand("switchWorld", save.world_id)}
          >
            切换到该时间线
          </button>
        ) : (
          <>
            <button
              className="save-action-load"
              onClick={() => void panelCommand("loadSave", save.id)}
            >
              读取
            </button>
            <button
              className="save-action-rename"
              onClick={() => useAppStore.setState({ renameSlotId: save.id })}
            >
              重命名
            </button>
            {!isAuto && (
              <button
                className="save-action-del"
                onClick={() => void panelCommand("deleteSave", save.id)}
              >
                删除
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function formatSlotTime(createdAt?: string) {
  const time = formatSaveTime(createdAt);
  return time.relative ? `${time.relative} · ${time.absolute}` : time.absolute;
}

/** 时间线内的一个存档点（槽位），紧凑行。 */
function SlotRow({ save, manage }: { save: SaveEntry; manage: boolean }) {
  const renameSlotId = useAppStore((state) => state.renameSlotId);
  const [name, setName] = useState(save.label || save.scene_name || "");
  const isAuto = save.id === "slot_000";
  const renaming = renameSlotId === save.id;
  return (
    <div className="slot-row" data-slot={save.id}>
      <div className="slot-row-info">
        {renaming ? (
          <span className="save-rename-form">
            <input
              autoFocus
              className="save-rename-input"
              maxLength={50}
              value={name}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter")
                  void panelCommand("renameSave", save.id, name);
                if (event.key === "Escape")
                  useAppStore.setState({ renameSlotId: null });
              }}
            />
            <button
              className="save-rename-confirm"
              aria-label="确认重命名"
              data-tooltip="确认重命名"
              onClick={() => void panelCommand("renameSave", save.id, name)}
            >
              ✓
            </button>
            <button
              className="save-rename-cancel"
              aria-label="取消重命名"
              data-tooltip="取消重命名"
              onClick={() => useAppStore.setState({ renameSlotId: null })}
            >
              ×
            </button>
          </span>
        ) : (
          <span className="slot-row-name">
            {isAuto ? "自动存档 · " : ""}
            {save.label || save.scene_name || "未知场景"}
          </span>
        )}
        <span className="slot-row-meta">{formatSlotTime(save.created_at)}</span>
      </div>
      <div className="slot-row-actions">
        <button
          className="save-action-load"
          onClick={() => void panelCommand("loadSave", save.id)}
        >
          读取
        </button>
        {manage && !isAuto && (
          <>
            <button
              className="save-action-rename"
              onClick={() => useAppStore.setState({ renameSlotId: save.id })}
            >
              重命名
            </button>
            <button
              className="save-action-del"
              onClick={() => void panelCommand("deleteSave", save.id)}
            >
              删除
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function SavePanel() {
  const open = useAppStore((state) => state.savePanelOpen);
  const mode = useAppStore((state) => state.savePanelMode);
  // 延迟关闭：退出动画期间保留面板内容，完全隐藏后再重置内部视图状态。
  const { rendered, closing } = useDelayedClose(open);
  const appMode = useAppStore((state) => state.mode);
  const saves = useAppStore((state) => state.saves);
  const worlds = useAppStore((state) => state.worlds);
  const adventures = useAppStore((state) => state.adventures);
  const adventuresReady = useAppStore((state) => state.adventuresReady);
  const activeWorldId = useAppStore((state) => state.activeWorldId);
  const latestBranchTurnId = useAppStore((state) => state.latestBranchTurnId);
  const [view, setView] = useState<
    { name: "adventures" } | { name: "timelines"; rootId: string }
  >({ name: "adventures" });
  const [archiveConfirmationId, setArchiveConfirmationId] = useState<
    string | null
  >(null);
  const [slotConfirmationId, setSlotConfirmationId] = useState<string | null>(
    null,
  );
  const [renamingSlotId, setRenamingSlotId] = useState<string | null>(null);
  const [slotName, setSlotName] = useState("");
  const [renamingWorldId, setRenamingWorldId] = useState<string | null>(null);
  const [worldName, setWorldName] = useState("");
  const [branchLabel, setBranchLabel] = useState("");
  const [selectedSaveId, setSelectedSaveId] = useState<string | null>(null);

  useEffect(() => {
    if (!rendered) {
      setView({ name: "adventures" });
      setArchiveConfirmationId(null);
      setSlotConfirmationId(null);
      setRenamingSlotId(null);
      setRenamingWorldId(null);
      setBranchLabel("");
      setSelectedSaveId(null);
    }
  }, [rendered]);

  // 面板内部“存档列表 ↔ 时间线”单挂载两阶段换场（面板背景恒定，
  // 旧页快速左/右出后新页进入，不会像开始页那样露出背景闪烁）。
  const viewTransition = usePhaseTransition(
    view,
    (item) =>
      item.name === "timelines" ? `timelines:${item.rootId}` : "adventures",
    { exitMs: 120, enterMs: 200 },
  );
  const displayedView = viewTransition.displayed;

  const focusedAdventure =
    displayedView.name === "timelines"
      ? adventures.find(
          (item) => String(item.root_world_id || "") === displayedView.rootId,
        )
      : undefined;
  useEffect(() => {
    if (displayedView.name === "timelines" && !focusedAdventure) {
      setView({ name: "adventures" });
    }
  }, [displayedView, focusedAdventure]);

  useEffect(() => {
    if (
      archiveConfirmationId &&
      adventures.length > 0 &&
      !adventures.some((adventure) =>
        (adventure.timelines || []).some(
          (timeline) =>
            String(timeline.world_id || "") === archiveConfirmationId,
        ),
      )
    ) {
      setArchiveConfirmationId(null);
    }
  }, [archiveConfirmationId, adventures]);

  useEffect(() => {
    if (
      slotConfirmationId &&
      !adventures.some(
        (adventure) =>
          String(adventure.root_world_id || "") === slotConfirmationId,
      )
    ) {
      setSlotConfirmationId(null);
    }
    if (
      renamingSlotId &&
      !adventures.some(
        (adventure) => String(adventure.root_world_id || "") === renamingSlotId,
      )
    ) {
      setRenamingSlotId(null);
    }
  }, [slotConfirmationId, renamingSlotId, adventures]);

  useEffect(() => {
    if (selectedSaveId && !saves.some((save) => save.id === selectedSaveId)) {
      setSelectedSaveId(null);
    }
  }, [selectedSaveId, saves]);

  if (!rendered) return <div id="save-panel-overlay" className="hidden" />;

  const manage = mode !== "load";
  const overlayClass = closing ? "overlay-closing" : undefined;
  // 换场阶段 class：leaving 播旧页出场，entering 播新页入场；方向由目标页决定
  const pageClass =
    viewTransition.phase === "idle"
      ? "save-panel-page"
      : `save-panel-page panel-page-${viewTransition.phase} page-${
          view.name === "timelines" ? "forward" : "backward"
        }`;
  const closeButton = (
    <button
      id="save-panel-close"
      className="panel-close-btn"
      onClick={() => void panelCommand("closeSavePanel")}
    >
      关闭
    </button>
  );

  // 旧服务端不下发 adventure_list、联机房间协议虽复用本地会话初始化序列
  // （会带一条空 adventure_list）但房间本就绑定单一世界：联机一律回退到
  // 平铺存档列表，世界区沿用旧的整树展示。
  if (!adventuresReady || appMode !== "local") {
    return (
      <div
        id="save-panel-overlay"
        className={overlayClass}
        data-mode={mode}
        onMouseDown={(event) => {
          if (event.target === event.currentTarget)
            void panelCommand("closeSavePanel");
        }}
      >
        <div id="save-panel">
          <div id="save-panel-header">
            <h3 id="save-panel-title">
              {mode === "load" ? "从存档开始" : "存档管理"}
            </h3>
            {closeButton}
          </div>
          <div id="save-panel-body">
            {worlds.length > 0 && (
              <section
                id="world-panel-section"
                aria-labelledby="world-panel-heading"
              >
                <div id="world-panel-heading">时间线</div>
                <div id="world-panel-list">
                  {worlds.map((world) => {
                    const id = String(world.world_id || "");
                    const active =
                      Boolean(world.active) || id === activeWorldId;
                    const resumable = world.resumable !== false;
                    const switchDisabled = active || !resumable;
                    const canArchive =
                      appMode === "local" &&
                      !active &&
                      world.is_branch === true;
                    const confirmingArchive = archiveConfirmationId === id;
                    return (
                      <div
                        className={`world-entry${active ? " active" : ""}`}
                        key={id}
                      >
                        <div className="world-entry-info">
                          <div className="world-entry-title">
                            {world.label ||
                              (world.is_branch ? "时间线分支" : "主时间线")}
                          </div>
                          <div className="world-entry-meta">
                            {world.scene_name || "未知场景"} ·{" "}
                            {world.character_name || "未知调查员"}
                          </div>
                        </div>
                        <div className="world-entry-actions">
                          <button
                            className="world-switch-button"
                            disabled={switchDisabled}
                            aria-label={
                              active
                                ? "当前时间线"
                                : !resumable
                                  ? "该时间线没有可继续的存档"
                                  : `切换到${world.label || "此时间线"}`
                            }
                            onClick={() => void panelCommand("switchWorld", id)}
                          >
                            {active ? "当前" : resumable ? "↪" : "无存档"}
                          </button>
                          {canArchive &&
                            (confirmingArchive ? (
                              <div
                                className="world-archive-confirmation"
                                role="group"
                                aria-label={`确认删除${world.label || "此时间线"}`}
                              >
                                <span>删除此分支？</span>
                                <button
                                  type="button"
                                  className="world-archive-confirm"
                                  onClick={() => {
                                    setArchiveConfirmationId(null);
                                    panelCommand("archiveWorld", id);
                                  }}
                                >
                                  确认删除
                                </button>
                                <button
                                  type="button"
                                  className="world-archive-cancel"
                                  onClick={() => setArchiveConfirmationId(null)}
                                >
                                  取消
                                </button>
                              </div>
                            ) : (
                              <button
                                type="button"
                                className="world-archive-button"
                                onClick={() => setArchiveConfirmationId(id)}
                              >
                                删除时间线
                              </button>
                            ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
            {manage && (
              <button
                id="save-panel-new"
                className="save-new-btn"
                onClick={() => void panelCommand("createSave")}
              >
                新建存档
              </button>
            )}
            <div id="save-panel-list">
              {saves.length ? (
                saves.map((save, index) => (
                  <SaveRow
                    key={`${save.world_id || ""}:${save.id}`}
                    save={save}
                    latest={index === 0}
                    selected={save.id === selectedSaveId}
                    onSelect={setSelectedSaveId}
                  />
                ))
              ) : (
                <div className="save-empty">暂无存档</div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---- 新两级结构：存档（一次游戏）→ 时间线（存档内分支）----
  const renderTimelineRow = (timeline: TimelineEntry) => {
    const id = String(timeline.world_id || "");
    const active = Boolean(timeline.active) || id === activeWorldId;
    const resumable = timeline.resumable !== false;
    const isBranch = Boolean(timeline.is_branch);
    const canArchive = appMode === "local" && !active && isBranch;
    const canRename = appMode === "local";
    const confirmingArchive = archiveConfirmationId === id;
    const renaming = renamingWorldId === id;
    const timelineSaves = saves.filter((save) =>
      save.world_id ? String(save.world_id) === id : active,
    );
    const depth = Math.max(0, Number(timeline.depth || (isBranch ? 1 : 0)));
    return (
      <div
        className={`timeline-entry${active ? " active" : ""}${isBranch ? " branch" : ""}`}
        style={isBranch ? { marginLeft: `${(depth - 1) * 22}px` } : undefined}
        key={id}
        data-world={id}
      >
        <div className="timeline-row">
          <div className="timeline-info">
            {renaming ? (
              <span className="save-rename-form">
                <input
                  autoFocus
                  className="save-rename-input"
                  maxLength={50}
                  value={worldName}
                  onChange={(event) => setWorldName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      void panelCommand("renameWorld", id, worldName);
                      setRenamingWorldId(null);
                    }
                    if (event.key === "Escape") setRenamingWorldId(null);
                  }}
                />
                <button
                  className="save-rename-confirm"
                  aria-label="确认重命名时间线"
                  data-tooltip="确认重命名"
                  onClick={() => {
                    void panelCommand("renameWorld", id, worldName);
                    setRenamingWorldId(null);
                  }}
                >
                  ✓
                </button>
                <button
                  className="save-rename-cancel"
                  aria-label="取消重命名时间线"
                  data-tooltip="取消重命名"
                  onClick={() => setRenamingWorldId(null)}
                >
                  ×
                </button>
              </span>
            ) : (
              <span className="timeline-title">
                {isBranch && <span className="timeline-branch-mark">└</span>}
                {timeline.label || (isBranch ? "时间线分支" : "主时间线")}
                {active && <span className="timeline-badge">当前</span>}
              </span>
            )}
            <span className="timeline-meta">
              {timeline.scene_name || "未知场景"} ·{" "}
              {timeline.character_name || "未知调查员"} ·{" "}
              {formatSaveTime(timeline.updated_at).relative || "未知时间"} ·{" "}
              {timeline.save_count ?? 0} 个存档点
            </span>
          </div>
          <div className="timeline-actions">
            {active ? (
              <button
                className="timeline-resume"
                onClick={() => panelCommand("resumeTimeline", id, true)}
              >
                继续游戏
              </button>
            ) : resumable ? (
              <button
                className="timeline-resume"
                onClick={() => panelCommand("resumeTimeline", id, false)}
              >
                从此处继续
              </button>
            ) : (
              <button
                className="timeline-resume"
                disabled
                aria-label="该时间线没有可继续的存档"
              >
                无存档
              </button>
            )}
            {canRename && !renaming && (
              <button
                className="timeline-rename"
                onClick={() => {
                  setRenamingWorldId(id);
                  setWorldName(
                    timeline.label || (isBranch ? "时间线分支" : "主时间线"),
                  );
                }}
              >
                重命名
              </button>
            )}
            {canArchive &&
              (confirmingArchive ? (
                <span
                  className="world-archive-confirmation"
                  role="group"
                  aria-label={`确认删除${timeline.label || "此时间线"}`}
                >
                  <span>删除此分支？</span>
                  <button
                    type="button"
                    className="world-archive-confirm"
                    onClick={() => {
                      setArchiveConfirmationId(null);
                      panelCommand("archiveWorld", id);
                    }}
                  >
                    确认
                  </button>
                  <button
                    type="button"
                    className="world-archive-cancel"
                    onClick={() => setArchiveConfirmationId(null)}
                  >
                    取消
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="timeline-archive"
                  onClick={() => setArchiveConfirmationId(id)}
                >
                  删除
                </button>
              ))}
          </div>
        </div>
        {active && timelineSaves.length > 0 && (
          <div className="timeline-slots">
            {timelineSaves.map((save) => (
              <SlotRow key={save.id} save={save} manage={manage} />
            ))}
          </div>
        )}
        {active && manage && (
          <div className="timeline-manage">
            <button
              className="save-new-inline"
              onClick={() => void panelCommand("createSave")}
            >
              新建存档点
            </button>
            {latestBranchTurnId && (
              <span className="timeline-branch-form">
                <input
                  className="timeline-branch-input"
                  maxLength={50}
                  placeholder="分支名（可留空）"
                  value={branchLabel}
                  onChange={(event) => setBranchLabel(event.target.value)}
                />
                <button
                  className="timeline-branch-create"
                  onClick={() => {
                    void panelCommand(
                      "createBranchFromCurrentTurn",
                      branchLabel,
                    );
                    setBranchLabel("");
                  }}
                >
                  从当前进度创建分支
                </button>
              </span>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      id="save-panel-overlay"
      className={overlayClass}
      data-mode={mode}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget)
          void panelCommand("closeSavePanel");
      }}
    >
      <div id="save-panel">
        {displayedView.name === "adventures" ? (
          <div className={pageClass}>
            <div id="save-panel-header">
              <h3 id="save-panel-title">
                {mode === "load" ? "从存档开始" : "存档管理"}
              </h3>
              {closeButton}
            </div>
            <div id="save-panel-body" data-testid="save-panel-adventures">
              {adventures.length === 0 ? (
                <div className="save-empty-slots">
                  <div className="save-empty-slots-title">还没有存档位</div>
                  <div className="save-empty-slots-hint">
                    点击「开始新游戏」创建 SAVE 01。
                  </div>
                </div>
              ) : (
                <div className="adventure-list">
                  {adventures.map((adventure) => {
                    const rootId = String(adventure.root_world_id || "");
                    const current = Boolean(adventure.active);
                    const moduleTitle =
                      adventure.module_title ||
                      adventure.module_name ||
                      "未知模组";
                    const slotNameShown = String(adventure.slot_name || "");
                    const title = slotNameShown || moduleTitle;
                    const slotNo = adventure.slot_index
                      ? `SAVE ${String(adventure.slot_index).padStart(2, "0")}`
                      : "SAVE --";
                    const savedTime = formatSaveTime(adventure.updated_at);
                    const progress = [
                      adventure.character_name || "未知调查员",
                      adventure.scene_name || "未知场景",
                      adventure.turn_count
                        ? `第 ${adventure.turn_count} 回合`
                        : "",
                    ]
                      .filter(Boolean)
                      .join(" · ");
                    const confirmingSlot = slotConfirmationId === rootId;
                    const renamingSlot = renamingSlotId === rootId;
                    const canRenameSlot = appMode === "local";
                    const canDeleteSlot = appMode === "local" && !current;
                    return (
                      <div
                        className={`adventure-card${current ? " current" : ""}`}
                        key={rootId}
                        data-adventure={rootId}
                      >
                        <div className="adventure-card-main">
                          <div
                            className="adventure-card-info"
                            onClick={() => {
                              if (!renamingSlot)
                                setView({ name: "timelines", rootId });
                            }}
                          >
                            <div className="adventure-slot-line">
                              <span className="adventure-slot-no">
                                {slotNo}
                              </span>
                              {current && (
                                <span className="adventure-badge">当前</span>
                              )}
                            </div>
                            {renamingSlot ? (
                              <span className="save-rename-form">
                                <input
                                  autoFocus
                                  className="save-rename-input"
                                  maxLength={50}
                                  placeholder={moduleTitle}
                                  value={slotName}
                                  onChange={(event) =>
                                    setSlotName(event.target.value)
                                  }
                                  onKeyDown={(event) => {
                                    if (event.key === "Enter") {
                                      void panelCommand(
                                        "renameAdventure",
                                        rootId,
                                        slotName,
                                      );
                                      setRenamingSlotId(null);
                                    }
                                    if (event.key === "Escape")
                                      setRenamingSlotId(null);
                                  }}
                                />
                                <button
                                  className="save-rename-confirm"
                                  aria-label="确认重命名存档"
                                  data-tooltip="确认重命名"
                                  onClick={() => {
                                    void panelCommand(
                                      "renameAdventure",
                                      rootId,
                                      slotName,
                                    );
                                    setRenamingSlotId(null);
                                  }}
                                >
                                  ✓
                                </button>
                                <button
                                  className="save-rename-cancel"
                                  aria-label="取消重命名存档"
                                  data-tooltip="取消重命名"
                                  onClick={() => setRenamingSlotId(null)}
                                >
                                  ×
                                </button>
                              </span>
                            ) : (
                              <div className="adventure-card-title">
                                {title}
                              </div>
                            )}
                            <div className="adventure-card-meta">
                              {progress}
                            </div>
                            <div className="adventure-card-meta dim">
                              {slotNameShown ? `${moduleTitle} · ` : ""}
                              最后保存 {savedTime.absolute}
                              {savedTime.relative
                                ? `（${savedTime.relative}）`
                                : ""}
                            </div>
                          </div>
                          <div className="adventure-card-actions">
                            <button
                              className="adventure-resume"
                              disabled={!adventure.resume_world_id}
                              onClick={() =>
                                void panelCommand("resumeAdventure", adventure)
                              }
                            >
                              {manage ? "继续游戏" : "读取"}
                            </button>
                            <div className="adventure-card-sub-actions">
                              <button
                                className="adventure-manage"
                                onClick={() =>
                                  setView({ name: "timelines", rootId })
                                }
                              >
                                {manage ? "管理时间线" : "时间线"}
                              </button>
                              {canRenameSlot && !renamingSlot && (
                                <button
                                  type="button"
                                  className="adventure-rename"
                                  onClick={() => {
                                    setRenamingSlotId(rootId);
                                    setSlotName(slotNameShown);
                                  }}
                                >
                                  重命名
                                </button>
                              )}
                              {canDeleteSlot && !confirmingSlot && (
                                <button
                                  type="button"
                                  className="adventure-delete"
                                  onClick={() => setSlotConfirmationId(rootId)}
                                >
                                  删除存档
                                </button>
                              )}
                            </div>
                          </div>
                        </div>
                        {canDeleteSlot && confirmingSlot && (
                          <div
                            className="adventure-delete-confirmation"
                            role="group"
                            aria-label={`确认删除${slotNo}`}
                          >
                            <span>
                              删除此存档位？其 {adventure.timeline_count ?? 1}{" "}
                              条时间线将一并归档（数据保留可恢复）。
                            </span>
                            <button
                              type="button"
                              className="world-archive-confirm"
                              onClick={() => {
                                setSlotConfirmationId(null);
                                panelCommand("archiveAdventure", rootId);
                              }}
                            >
                              确认删除
                            </button>
                            <button
                              type="button"
                              className="world-archive-cancel"
                              onClick={() => setSlotConfirmationId(null)}
                            >
                              取消
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className={pageClass}>
            <div id="save-panel-header">
              <button
                className="save-panel-back"
                onClick={() => setView({ name: "adventures" })}
              >
                ← 存档列表
              </button>
              <h3 id="save-panel-title">
                {focusedAdventure?.module_title ||
                  focusedAdventure?.module_name ||
                  "存档"}
                <span className="save-panel-subtitle">
                  {focusedAdventure?.character_name || ""}
                </span>
              </h3>
              {closeButton}
            </div>
            <div id="save-panel-body" data-testid="save-panel-timelines">
              {(focusedAdventure?.timelines || []).some(
                (timeline) => !timeline.is_branch,
              ) && (
                <section className="timeline-section">
                  <div className="timeline-section-heading">主时间线</div>
                  {(focusedAdventure?.timelines || [])
                    .filter((timeline) => !timeline.is_branch)
                    .map(renderTimelineRow)}
                </section>
              )}
              {(focusedAdventure?.timelines || []).some(
                (timeline) => timeline.is_branch,
              ) && (
                <section className="timeline-section">
                  <div className="timeline-section-heading">分支时间线</div>
                  {(focusedAdventure?.timelines || [])
                    .filter((timeline) => timeline.is_branch)
                    .map(renderTimelineRow)}
                </section>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
