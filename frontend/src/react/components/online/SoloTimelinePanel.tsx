import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  archiveSoloTimeline,
  fetchSoloTimelines,
  renameSoloTimeline,
  switchSoloTimeline,
  type SoloTimeline,
  type WorldSummary,
} from "../../../api/worlds";
import { enterRoom, errorMessage, refreshWorlds } from "../../../online";

function formatRelativeTime(value?: string): string {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知时间";
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}小时前`;
  return `${Math.floor(minutes / 1440)}天前`;
}

type PanelStatus = "loading" | "ready" | "error";

/**
 * 云端单人大厅的就地时间线管理面板：纯 HTTP 控制面驱动
 * （list/switch/rename/archive），完全不建立房间连接。
 * 视觉复用存档面板的时间线行（.timeline-entry/.timeline-row 等），
 * 容器与“离开冒险”浮层同一套深色木/黄铜体系。
 */
export function SoloTimelinePanel({
  world,
  title,
  onClose,
}: {
  world: WorldSummary;
  title: string;
  onClose: () => void;
}) {
  const rootWorldId = world.world_id;
  const [status, setStatus] = useState<PanelStatus>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [timelines, setTimelines] = useState<SoloTimeline[]>([]);
  const [activeWorldId, setActiveWorldId] = useState<string | null>(null);
  // mutation 报错内联挂在面板底部（detail 原文），不进加载错误态。
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmingArchiveId, setConfirmingArchiveId] = useState<string | null>(
    null,
  );
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setStatus("loading");
    setLoadError(null);
    try {
      const data = await fetchSoloTimelines(rootWorldId);
      setTimelines(data.worlds ?? []);
      setActiveWorldId(data.active_world_id);
      setStatus("ready");
    } catch (error) {
      setLoadError(errorMessage(error, "无法读取时间线列表"));
      setStatus("error");
    }
  }, [rootWorldId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    dialogRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  /** mutation 成功后刷新面板数据，并同步大厅列表（resume_world_id 可能已变）。 */
  async function reloadAfterMutation() {
    await load();
    void refreshWorlds();
  }

  async function resume(timeline: SoloTimeline) {
    const id = timeline.world_id;
    const active = Boolean(timeline.active) || id === activeWorldId;
    setActionError(null);
    if (active) {
      // 当前时间线：与“继续冒险”同一目标（树根指针或存档根本身）。
      await enterRoom(world.resume_world_id || world.world_id);
      return;
    }
    setBusy(true);
    try {
      await switchSoloTimeline(rootWorldId, id);
    } catch (error) {
      setActionError(errorMessage(error, "切换时间线失败，请重试"));
      setBusy(false);
      return;
    }
    setBusy(false);
    await enterRoom(id);
  }

  async function confirmRename(timeline: SoloTimeline) {
    const label = renameValue.trim();
    setRenamingId(null);
    if (!label || busy) return;
    setActionError(null);
    setBusy(true);
    try {
      await renameSoloTimeline(rootWorldId, timeline.world_id, label);
    } catch (error) {
      setActionError(errorMessage(error, "重命名失败，请重试"));
      setBusy(false);
      return;
    }
    setBusy(false);
    await reloadAfterMutation();
  }

  async function confirmArchive(timeline: SoloTimeline) {
    setConfirmingArchiveId(null);
    if (busy) return;
    setActionError(null);
    setBusy(true);
    try {
      await archiveSoloTimeline(rootWorldId, timeline.world_id);
    } catch (error) {
      setActionError(errorMessage(error, "删除时间线失败，请重试"));
      setBusy(false);
      return;
    }
    setBusy(false);
    await reloadAfterMutation();
  }

  const renderRow = (timeline: SoloTimeline) => {
    const id = timeline.world_id;
    const active = Boolean(timeline.active) || id === activeWorldId;
    const isBranch = Boolean(timeline.is_branch);
    const resumable = timeline.resumable !== false;
    const depth = Math.max(0, Number(timeline.depth || (isBranch ? 1 : 0)));
    const renaming = renamingId === id;
    const confirmingArchive = confirmingArchiveId === id;
    const fallbackLabel = isBranch ? "时间线分支" : "主时间线";
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
                  value={renameValue}
                  aria-label="时间线名称"
                  onChange={(event) => setRenameValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void confirmRename(timeline);
                    if (event.key === "Escape") setRenamingId(null);
                  }}
                />
                <button
                  type="button"
                  className="save-rename-confirm"
                  aria-label="确认重命名时间线"
                  onClick={() => void confirmRename(timeline)}
                >
                  ✓
                </button>
                <button
                  type="button"
                  className="save-rename-cancel"
                  aria-label="取消重命名时间线"
                  onClick={() => setRenamingId(null)}
                >
                  ×
                </button>
              </span>
            ) : (
              <span className="timeline-title">
                {isBranch && <span className="timeline-branch-mark">└</span>}
                {timeline.label || fallbackLabel}
                {active && <span className="timeline-badge">当前</span>}
              </span>
            )}
            <span className="timeline-meta">
              {timeline.scene_name || "未知场景"} ·{" "}
              {timeline.character_name || "未知调查员"} ·{" "}
              {formatRelativeTime(timeline.updated_at)}
            </span>
          </div>
          <div className="timeline-actions">
            {active || resumable ? (
              <button
                type="button"
                className="timeline-resume"
                disabled={busy}
                onClick={() => void resume(timeline)}
              >
                继续游戏
              </button>
            ) : (
              <button
                type="button"
                className="timeline-resume"
                disabled
                aria-label="该时间线没有可继续的存档"
              >
                无存档
              </button>
            )}
            {!renaming && (
              <button
                type="button"
                className="timeline-rename"
                disabled={busy}
                onClick={() => {
                  setRenamingId(id);
                  setRenameValue(timeline.label || fallbackLabel);
                }}
              >
                重命名
              </button>
            )}
            {!active &&
              isBranch &&
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
                    disabled={busy}
                    onClick={() => void confirmArchive(timeline)}
                  >
                    确认
                  </button>
                  <button
                    type="button"
                    className="world-archive-cancel"
                    disabled={busy}
                    onClick={() => setConfirmingArchiveId(null)}
                  >
                    取消
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="timeline-archive"
                  disabled={busy}
                  onClick={() => setConfirmingArchiveId(id)}
                >
                  删除
                </button>
              ))}
          </div>
        </div>
      </div>
    );
  };

  const mainTimelines = timelines.filter((timeline) => !timeline.is_branch);
  const branchTimelines = timelines.filter((timeline) => timeline.is_branch);

  return createPortal(
    <div
      className="solo-timeline-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="solo-timeline-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="solo-timeline-panel-title"
        tabIndex={-1}
      >
        <div className="solo-timeline-header">
          <div className="solo-timeline-heading">
            <h2 id="solo-timeline-panel-title">{title}</h2>
            <p className="solo-timeline-subtitle">时间线</p>
          </div>
          <button
            type="button"
            className="solo-timeline-close"
            aria-label="关闭时间线面板"
            disabled={busy}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="solo-timeline-body">
          {status === "loading" && (
            <p className="online-loading" role="status">
              正在读取时间线……
            </p>
          )}
          {status === "error" && (
            <div className="online-empty">
              <p className="online-notice online-notice--error" role="alert">
                {loadError ?? "无法读取时间线列表"}
              </p>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => void load()}
              >
                重试
              </button>
            </div>
          )}
          {status === "ready" && (
            <>
              {mainTimelines.length > 0 && (
                <section className="timeline-section">
                  <div className="timeline-section-heading">主时间线</div>
                  {mainTimelines.map(renderRow)}
                </section>
              )}
              {branchTimelines.length > 0 && (
                <section className="timeline-section">
                  <div className="timeline-section-heading">分支时间线</div>
                  {branchTimelines.map(renderRow)}
                </section>
              )}
              {timelines.length === 0 && (
                <p className="online-loading" role="status">
                  这份冒险还没有可管理的时间线
                </p>
              )}
            </>
          )}
          {actionError && (
            <p className="online-notice online-notice--error" role="alert">
              {actionError}
            </p>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
