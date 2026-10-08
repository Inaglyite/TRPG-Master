import { useRef, useState } from "react";

import {
  createSoloWorld,
  deleteSoloWorld,
  enterRoom,
  logout,
  refreshWorlds,
  ensureModules,
} from "../../../online";
import { desktopBridge } from "../../../desktop";
import { useAppStore } from "../../../state/app-store";
import { resetOnlineState, useOnlineStore } from "../../../state/online-store";
import { ModuleSelect } from "../ModuleSelect";
import { usePhaseTransition, useDelayedClose } from "../transitions";
import { roomStatusLabel } from "./room-status";
import { SoloTimelinePanel } from "./SoloTimelinePanel";
import { PlayStylePicker } from "./PlayStylePicker";
import type { KeeperMode } from "../../../protocol/structured";
import { AdventureArchiveConfirmation } from "./AdventureArchiveConfirmation";

function formatTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

/**
 * 云端单人“我的冒险”：列出 play_mode=solo 的私密世界，可新建/继续/归档。
 * 视觉与本地开始页同一体系（主题背景裸排版 + start-brand 品牌区 +
 * adventure-card 存档卡 + start-art-button 黄铜 CTA），写通路保持
 * HTTP worlds/createSoloWorld 不变。删除即归档，保留二次确认。
 * 时间线管理由大厅内的 SoloTimelinePanel 就地完成（HTTP 控制面，
 * 不进房间）；只有“继续冒险”按钮会建立房间连接。
 */
export function SoloLobbyScreen() {
  const user = useOnlineStore((state) => state.user);
  const worlds = useOnlineStore((state) => state.worlds);
  const worldsStatus = useOnlineStore((state) => state.worldsStatus);
  const worldsError = useOnlineStore((state) => state.worldsError);
  const modules = useOnlineStore((state) => state.modules);
  const modulesStatus = useOnlineStore((state) => state.modulesStatus);
  const modulesError = useOnlineStore((state) => state.modulesError);
  const authError = useOnlineStore((state) => state.authError);
  const createBusy = useOnlineStore((state) => state.createBusy);
  const createError = useOnlineStore((state) => state.createError);
  const authBusy = useOnlineStore((state) => state.authBusy);
  const setMode = useAppStore((state) => state.setMode);

  const [moduleId, setModuleId] = useState("");
  const [worldName, setWorldName] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [structuredWorld, setStructuredWorld] = useState(false);
  const [keeperMode, setKeeperMode] = useState<KeeperMode>("human");
  // 「开始新冒险」CTA ↔ 创建卡成对换场：CTA 淡出下沉后创建卡弹入，
  // 「收起」反向播回；reduced-motion 由钩子直接落定。
  const createSwap = usePhaseTransition(
    createOpen ? ("form" as const) : ("cta" as const),
    (view) => view,
    { exitMs: 160, enterMs: 260 },
  );
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const archiveInFlight = useRef(false);
  const archiveTrigger = useRef<HTMLButtonElement | null>(null);
  // 归档确认抽屉：取消/换卡时保持挂载 170ms 播收回动画，再真正卸载；
  // lastConfirmId 让退出动画仍落在原来那张卡上。
  const lastArchiveConfirmId = useRef<string | null>(null);
  if (confirmingDelete) lastArchiveConfirmId.current = confirmingDelete;
  const archiveSwap = useDelayedClose(confirmingDelete !== null, 170);
  const renderedArchiveId = archiveSwap.rendered
    ? (confirmingDelete ?? lastArchiveConfirmId.current)
    : null;
  // 删除报错内联挂在被删除的冒险卡上（worldId + 消息），不进创建卡。
  const [deleteError, setDeleteError] = useState<{
    worldId: string;
    message: string;
  } | null>(null);

  const soloWorlds = worlds.filter(
    (world) => world.metadata?.play_mode === "solo",
  );
  const moduleTitle = (id: string) =>
    modules.find((module) => module.id === id)?.title ?? id;
  const selectedModule = moduleId || modules[0]?.id || "";
  // solo 世界的连接目标是树根指针指向的当前时间线（缺省即自身）。
  const resumeWorldId = (world: (typeof soloWorlds)[number]) =>
    world.resume_world_id || world.world_id;
  const adventureTitle = (world: (typeof soloWorlds)[number]) =>
    world.metadata?.name || moduleTitle(world.module);
  // 时间线管理在大厅就地完成（HTTP 控制面），只有“继续冒险”才进房间。
  const [timelineWorld, setTimelineWorld] = useState<
    (typeof soloWorlds)[number] | null
  >(null);

  async function backToModeSelect() {
    const bridge = desktopBridge();
    if (bridge) {
      const result = await bridge.returnToLauncher();
      if (!result.ok) {
        useOnlineStore.setState({
          worldsError: result.error ?? "无法返回模式选择",
        });
      }
      return;
    }
    resetOnlineState();
    setMode("select");
  }

  async function confirmDelete(worldId: string) {
    if (archiveInFlight.current) return;
    archiveInFlight.current = true;
    setDeleteError(null);
    setDeleteBusy(true);
    try {
      const error = await deleteSoloWorld(worldId);
      if (error) setDeleteError({ worldId, message: error });
      else setConfirmingDelete(null);
    } finally {
      archiveInFlight.current = false;
      setDeleteBusy(false);
    }
  }

  return (
    <div
      className="online-start-view solo-lobby lobby-screen"
      data-testid="solo-lobby"
    >
      <header className="solo-lobby-header">
        <div className="start-brand">
          <h1 className="online-title">我的冒险</h1>
          <p className="online-subtitle">云端私密单人世界，只有你能进入</p>
        </div>
        <div className="solo-lobby-user online-account">
          <button
            type="button"
            className="btn-ghost lobby-library-link"
            onClick={() => useAppStore.getState().setCharacterLibraryOpen(true)}
          >
            角色库
          </button>
          <span className="online-user" title={user?.username}>
            {user?.username}
          </span>
          <button
            type="button"
            className="account-logout"
            disabled={authBusy}
            onClick={() => void logout()}
          >
            {authBusy ? "正在退出…" : "退出登录"}
          </button>
        </div>
      </header>
      {authError && (
        <p className="online-notice online-notice--error" role="alert">
          {authError}
        </p>
      )}
      {modulesStatus === "error" && (
        <div className="online-empty">
          <p role="alert">{modulesError || "无法读取模组列表"}</p>
          <button
            className="btn-ghost lobby-refresh"
            onClick={() => void ensureModules()}
          >
            重试读取模组
          </button>
        </div>
      )}

      <section
        className="solo-lobby-section"
        aria-labelledby="solo-worlds-title"
      >
        <div className="solo-lobby-section-head">
          <h2 id="solo-worlds-title">进行中的冒险</h2>
          <button
            type="button"
            className="btn-ghost lobby-refresh"
            onClick={() => void refreshWorlds()}
            disabled={worldsStatus === "loading"}
          >
            {worldsStatus === "loading" ? "刷新中……" : "刷新"}
          </button>
        </div>

        {worldsStatus === "loading" && soloWorlds.length === 0 && (
          <p className="online-loading" role="status">
            正在读取冒险列表……
          </p>
        )}
        {worldsStatus === "error" && (
          <div className="online-empty">
            <p className="online-notice online-notice--error" role="alert">
              {worldsError ?? "无法读取冒险列表"}
            </p>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => void refreshWorlds()}
            >
              重试
            </button>
          </div>
        )}
        {worldsStatus === "ready" && soloWorlds.length === 0 && (
          <div className="online-empty lobby-empty">
            <p className="lobby-empty-mark" aria-hidden="true" />
            <p>还没有云端单人冒险。在下方选择模组，开始你的第一次调查。</p>
          </div>
        )}
        {soloWorlds.length > 0 && (
          <div className="solo-world-list">
            {soloWorlds.map((world) => (
              <div
                className="solo-world-item"
                key={world.world_id}
                data-world={world.world_id}
              >
                <div className="adventure-card">
                  <div className="adventure-card-main">
                    <button
                      type="button"
                      className="adventure-card-info"
                      aria-label={`${adventureTitle(world)}：管理时间线`}
                      disabled={deleteBusy}
                      onClick={() => setTimelineWorld(world)}
                    >
                      <span className="adventure-slot-line">
                        <span className="adventure-slot-no">云端存档</span>
                        {world.metadata?.room_status && (
                          <span className="adventure-badge">
                            {roomStatusLabel(world.metadata.room_status)}
                          </span>
                        )}
                      </span>
                      <span className="adventure-card-title">
                        {world.metadata?.name || moduleTitle(world.module)}
                      </span>
                      <span className="adventure-card-meta">
                        {moduleTitle(world.module)}
                      </span>
                      <span className="adventure-card-meta dim">
                        最后游玩 {formatTime(world.updated_at) || "未知"}
                      </span>
                    </button>
                    <div className="adventure-card-actions">
                      <button
                        type="button"
                        className="adventure-resume"
                        disabled={deleteBusy}
                        onClick={() => void enterRoom(resumeWorldId(world))}
                      >
                        继续冒险
                      </button>
                      <div className="adventure-card-sub-actions">
                        <button
                          type="button"
                          className="adventure-manage"
                          disabled={deleteBusy}
                          onClick={() => setTimelineWorld(world)}
                        >
                          管理时间线
                        </button>
                        {world.role === "owner" && (
                          <button
                            type="button"
                            className="adventure-delete"
                            disabled={deleteBusy}
                            onClick={(event) => {
                              setDeleteError(null);
                              archiveTrigger.current = event.currentTarget;
                              setConfirmingDelete(world.world_id);
                            }}
                          >
                            归档冒险
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
                {renderedArchiveId === world.world_id && (
                  <AdventureArchiveConfirmation
                    title={adventureTitle(world)}
                    busy={deleteBusy}
                    phase={archiveSwap.closing ? "closing" : "open"}
                    error={
                      deleteError?.worldId === world.world_id
                        ? deleteError.message
                        : null
                    }
                    onConfirm={() => void confirmDelete(world.world_id)}
                    onCancel={() => {
                      setConfirmingDelete(null);
                      setDeleteError(null);
                      archiveTrigger.current?.focus();
                    }}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section
        className="solo-lobby-section"
        aria-labelledby="solo-create-title"
      >
        <div className="solo-lobby-create-swap" data-phase={createSwap.phase}>
          {createSwap.displayed === "cta" ? (
            <button
              type="button"
              className="start-art-button art-plaque solo-lobby-create-cta"
              onClick={() => setCreateOpen(true)}
            >
              <span className="start-art-label">开始新冒险</span>
            </button>
          ) : (
            <div className="solo-lobby-create">
              <h2 id="solo-create-title">开始新冒险</h2>
              <p className="online-section-desc">
                选择模组，开一份只属于你的调查
              </p>
              <div className="online-inline-form lobby-form">
                <input
                  value={worldName}
                  onChange={(event) => setWorldName(event.target.value)}
                  placeholder="冒险名称（可选）"
                  aria-label="冒险名称"
                  disabled={createBusy}
                  maxLength={60}
                />
                <span id="solo-create-module-label" hidden>
                  选择模组
                </span>
                <ModuleSelect
                  options={modules}
                  value={selectedModule}
                  disabled={createBusy || modulesStatus !== "ready"}
                  labelledBy="solo-create-module-label"
                  listLabel="选择模组"
                  onSelect={(id) => setModuleId(id)}
                />
              </div>
              <PlayStylePicker
                structured={structuredWorld}
                keeperMode={keeperMode}
                disabled={createBusy}
                solo
                onChange={(value) => {
                  setStructuredWorld(value.structured);
                  setKeeperMode(value.keeperMode);
                }}
              />
              <div className="platform-create-actions">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={createBusy || !selectedModule}
                  onClick={() =>
                    void createSoloWorld(selectedModule, worldName, {
                      structured: structuredWorld,
                      ...(structuredWorld ? { keeperMode } : {}),
                    })
                  }
                >
                  {createBusy ? "创建中……" : "创建冒险"}
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={createBusy}
                  onClick={() => setCreateOpen(false)}
                >
                  收起
                </button>
              </div>
              {createError && (
                <p className="online-notice online-notice--error" role="alert">
                  {createError}
                </p>
              )}
            </div>
          )}
        </div>
      </section>

      <button
        type="button"
        className="start-menu-button solo-lobby-back"
        onClick={() => void backToModeSelect()}
      >
        ← 返回模式选择
      </button>

      {timelineWorld && (
        <SoloTimelinePanel
          world={timelineWorld}
          title={adventureTitle(timelineWorld)}
          onClose={() => setTimelineWorld(null)}
        />
      )}
    </div>
  );
}
