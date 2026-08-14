import { useState } from "react";

import {
  createSoloWorld,
  deleteSoloWorld,
  enterRoom,
  logout,
  refreshWorlds,
} from "../../../online";
import { desktopBridge } from "../../../desktop";
import { useAppStore } from "../../../state/app-store";
import { resetOnlineState, useOnlineStore } from "../../../state/online-store";
import { roomStatusLabel } from "./room-status";

function formatTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

/**
 * 云端单人“我的冒险”：列出 play_mode=solo 的私密世界，可新建/继续/删除。
 * 视觉与本地开始页同一体系（主题背景裸排版 + start-brand 品牌区 +
 * adventure-card 存档卡 + start-art-button 黄铜 CTA），写通路保持
 * HTTP worlds/createSoloWorld 不变。删除即归档，保留二次确认。
 */
export function SoloLobbyScreen() {
  const user = useOnlineStore((state) => state.user);
  const worlds = useOnlineStore((state) => state.worlds);
  const worldsStatus = useOnlineStore((state) => state.worldsStatus);
  const worldsError = useOnlineStore((state) => state.worldsError);
  const modules = useOnlineStore((state) => state.modules);
  const modulesStatus = useOnlineStore((state) => state.modulesStatus);
  const createBusy = useOnlineStore((state) => state.createBusy);
  const createError = useOnlineStore((state) => state.createError);
  const setMode = useAppStore((state) => state.setMode);

  const [moduleId, setModuleId] = useState("");
  const [worldName, setWorldName] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const soloWorlds = worlds.filter(
    (world) => world.metadata?.play_mode === "solo",
  );
  const moduleTitle = (id: string) =>
    modules.find((module) => module.id === id)?.title ?? id;
  const selectedModule = moduleId || modules[0]?.id || "";

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
    setConfirmingDelete(null);
    setDeleteBusy(true);
    await deleteSoloWorld(worldId);
    setDeleteBusy(false);
  }

  return (
    <div
      className="online-start-view solo-lobby lobby-screen"
      data-testid="solo-lobby"
    >
      <header className="solo-lobby-header">
        <div className="start-brand">
          <h1 className="online-title fx-glow">我的冒险</h1>
          <p className="online-subtitle">云端私密单人世界，只有你能进入</p>
        </div>
        <div className="solo-lobby-user">
          <span className="online-user" title={user?.id}>
            {user?.username}
          </span>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => void logout()}
          >
            退出登录
          </button>
        </div>
      </header>

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
                className="adventure-card"
                key={world.world_id}
                data-world={world.world_id}
              >
                <div className="adventure-card-main">
                  <div
                    className="adventure-card-info"
                    onClick={() => void enterRoom(world.world_id)}
                  >
                    <div className="adventure-slot-line">
                      <span className="adventure-slot-no">云端存档</span>
                      {world.metadata?.room_status && (
                        <span className="adventure-badge">
                          {roomStatusLabel(world.metadata.room_status)}
                        </span>
                      )}
                    </div>
                    <div className="adventure-card-title">
                      {world.metadata?.name || moduleTitle(world.module)}
                    </div>
                    <div className="adventure-card-meta">
                      {moduleTitle(world.module)}
                    </div>
                    <div className="adventure-card-meta dim">
                      最后游玩 {formatTime(world.updated_at) || "未知"}
                    </div>
                  </div>
                  <div className="adventure-card-actions">
                    <button
                      type="button"
                      className="adventure-resume"
                      onClick={() => void enterRoom(world.world_id)}
                    >
                      继续冒险
                    </button>
                    <div className="adventure-card-sub-actions">
                      {confirmingDelete === world.world_id ? (
                        <>
                          <button
                            type="button"
                            className="adventure-delete"
                            disabled={deleteBusy}
                            onClick={() => void confirmDelete(world.world_id)}
                          >
                            确认删除
                          </button>
                          <button
                            type="button"
                            className="adventure-manage"
                            disabled={deleteBusy}
                            onClick={() => setConfirmingDelete(null)}
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="adventure-delete"
                          disabled={deleteBusy}
                          onClick={() => setConfirmingDelete(world.world_id)}
                        >
                          删除存档
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section
        className="solo-lobby-section"
        aria-labelledby="solo-create-title"
      >
        {!createOpen ? (
          <button
            type="button"
            className="start-art-button solo-lobby-create-cta"
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
              <select
                value={selectedModule}
                onChange={(event) => setModuleId(event.target.value)}
                disabled={createBusy || modulesStatus !== "ready"}
                aria-label="选择模组"
              >
                {modulesStatus !== "ready" && <option>正在读取模组……</option>}
                {modules.map((module) => (
                  <option key={module.id} value={module.id}>
                    {module.title}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn-primary"
                disabled={createBusy || !selectedModule}
                onClick={() => void createSoloWorld(selectedModule, worldName)}
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
      </section>

      <button
        type="button"
        className="start-menu-button solo-lobby-back"
        onClick={() => void backToModeSelect()}
      >
        ← 返回模式选择
      </button>
    </div>
  );
}
