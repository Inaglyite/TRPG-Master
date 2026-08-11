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
 * 与多人大厅共用 worlds/modules 数据；solo 世界不能邀请或加入，删除即归档。
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
      className="online-box online-card online-card--wide lobby-screen solo-lobby-screen"
      data-testid="solo-lobby"
    >
      <header className="online-header">
        <div>
          <h1 className="online-title online-title--small">我的冒险</h1>
          <p className="online-subtitle">云端私密单人世界，只有你能进入</p>
        </div>
        <div className="online-header-side">
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
        className="online-section lobby-section"
        aria-labelledby="solo-worlds-title"
      >
        <div className="online-section-head">
          <div>
            <h2 id="solo-worlds-title">进行中的冒险</h2>
            <p className="online-section-desc">你的全部云端单人存档</p>
          </div>
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
          <ul className="room-list">
            {soloWorlds.map((world) => (
              <li key={world.world_id} className="solo-world-row">
                <button
                  type="button"
                  className="room-card"
                  onClick={() => void enterRoom(world.world_id)}
                >
                  <span className="room-card-top">
                    <span className="room-card-title">
                      {world.metadata?.name || moduleTitle(world.module)}
                    </span>
                    {world.metadata?.room_status && (
                      <span className="online-badge online-badge--status">
                        {roomStatusLabel(world.metadata.room_status)}
                      </span>
                    )}
                  </span>
                  <span className="room-card-module">
                    {moduleTitle(world.module)}
                  </span>
                  <span className="room-card-meta">
                    <span className="online-badge online-badge--solo">
                      单人
                    </span>
                    {formatTime(world.updated_at) && (
                      <span className="room-card-time">
                        {formatTime(world.updated_at)}
                      </span>
                    )}
                  </span>
                </button>
                {confirmingDelete === world.world_id ? (
                  <span className="member-action-group solo-world-actions">
                    <button
                      type="button"
                      className="btn-ghost online-danger"
                      disabled={deleteBusy}
                      onClick={() => void confirmDelete(world.world_id)}
                    >
                      确认删除
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={deleteBusy}
                      onClick={() => setConfirmingDelete(null)}
                    >
                      取消
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn-ghost solo-world-actions"
                    disabled={deleteBusy}
                    onClick={() => setConfirmingDelete(world.world_id)}
                  >
                    删除存档
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        className="online-section lobby-section"
        aria-labelledby="solo-create-title"
      >
        <div>
          <h2 id="solo-create-title">新建冒险</h2>
          <p className="online-section-desc">选择模组，开一份只属于你的调查</p>
        </div>
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
            {createBusy ? "创建中……" : "开始新的冒险"}
          </button>
        </div>
        {createError && (
          <p className="online-notice online-notice--error" role="alert">
            {createError}
          </p>
        )}
      </section>

      <button
        type="button"
        className="start-menu-button"
        onClick={() => void backToModeSelect()}
      >
        ← 返回模式选择
      </button>
    </div>
  );
}
