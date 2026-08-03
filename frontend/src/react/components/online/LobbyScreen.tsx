import { useState } from "react";

import {
  createRoom,
  enterRoom,
  joinWithToken,
  logout,
  refreshWorlds,
} from "../../../online";
import { desktopBridge } from "../../../desktop";
import { useAppStore } from "../../../state/app-store";
import { resetOnlineState, useOnlineStore } from "../../../state/online-store";

const ROLE_LABELS: Record<string, string> = {
  owner: "房主",
  player: "玩家",
  viewer: "旁观者",
};

function formatTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

/** 联机大厅：我的房间列表、创建房间、邀请码加入。 */
export function LobbyScreen() {
  const user = useOnlineStore((state) => state.user);
  const worlds = useOnlineStore((state) => state.worlds);
  const worldsStatus = useOnlineStore((state) => state.worldsStatus);
  const worldsError = useOnlineStore((state) => state.worldsError);
  const modules = useOnlineStore((state) => state.modules);
  const modulesStatus = useOnlineStore((state) => state.modulesStatus);
  const createBusy = useOnlineStore((state) => state.createBusy);
  const createError = useOnlineStore((state) => state.createError);
  const joinBusy = useOnlineStore((state) => state.joinBusy);
  const joinError = useOnlineStore((state) => state.joinError);
  const setMode = useAppStore((state) => state.setMode);

  const [moduleId, setModuleId] = useState("");
  const [roomName, setRoomName] = useState("");
  const [maxPlayers, setMaxPlayers] = useState(4);
  const [token, setToken] = useState("");

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

  return (
    <div className="online-box online-card online-card--wide lobby-screen">
      <header className="online-header">
        <div>
          <h1 className="online-title online-title--small">联机大厅</h1>
          <p className="online-subtitle">创建房间，或用邀请码加入朋友的调查</p>
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
        aria-labelledby="lobby-rooms-title"
      >
        <div className="online-section-head">
          <div>
            <h2 id="lobby-rooms-title">我的房间</h2>
            <p className="online-section-desc">你参与的全部调查档案</p>
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

        {worldsStatus === "loading" && worlds.length === 0 && (
          <p className="online-loading" role="status">
            正在读取房间列表……
          </p>
        )}
        {worldsStatus === "error" && (
          <div className="online-empty">
            <p className="online-notice online-notice--error" role="alert">
              {worldsError ?? "无法读取房间列表"}
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
        {worldsStatus === "ready" && worlds.length === 0 && (
          <div className="online-empty lobby-empty">
            <p className="lobby-empty-mark" aria-hidden="true">
              ❧
            </p>
            <p>还没有房间。创建一个，或在下方输入邀请码加入。</p>
          </div>
        )}
        {worlds.length > 0 && (
          <ul className="room-list">
            {worlds.map((world) => (
              <li key={world.world_id}>
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
                        {world.metadata.room_status}
                      </span>
                    )}
                  </span>
                  <span className="room-card-module">
                    {moduleTitle(world.module)}
                  </span>
                  <span className="room-card-meta">
                    <span
                      className={
                        world.role === "owner"
                          ? "online-badge online-badge--owner"
                          : "online-badge online-badge--role"
                      }
                    >
                      {ROLE_LABELS[world.role] ?? world.role}
                    </span>
                    {typeof world.member_count === "number" && (
                      <span className="room-card-count">
                        {world.member_count}
                        {world.metadata?.max_players
                          ? `/${world.metadata.max_players}`
                          : ""}{" "}
                        人
                      </span>
                    )}
                    {formatTime(world.updated_at) && (
                      <span className="room-card-time">
                        {formatTime(world.updated_at)}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        className="online-section lobby-section"
        aria-labelledby="lobby-create-title"
      >
        <div>
          <h2 id="lobby-create-title">创建房间</h2>
          <p className="online-section-desc">
            选择模组与人数，开一份新的调查档案
          </p>
        </div>
        <div className="online-inline-form lobby-form">
          <input
            value={roomName}
            onChange={(event) => setRoomName(event.target.value)}
            placeholder="房间名称（可选）"
            aria-label="房间名称"
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
          <select
            value={maxPlayers}
            onChange={(event) => setMaxPlayers(Number(event.target.value))}
            disabled={createBusy}
            aria-label="人数上限"
          >
            {[2, 3, 4].map((count) => (
              <option key={count} value={count}>
                {count} 人
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-primary"
            disabled={createBusy || !selectedModule}
            onClick={() =>
              void createRoom(selectedModule, roomName, maxPlayers)
            }
          >
            {createBusy ? "创建中……" : "创建房间"}
          </button>
        </div>
        {createError && (
          <p className="online-notice online-notice--error" role="alert">
            {createError}
          </p>
        )}
      </section>

      <section
        className="online-section lobby-section"
        aria-labelledby="lobby-join-title"
      >
        <div>
          <h2 id="lobby-join-title">邀请码加入</h2>
          <p className="online-section-desc">输入朋友分享的邀请码</p>
        </div>
        <div className="online-inline-form lobby-form">
          <input
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="输入邀请码"
            aria-label="邀请码"
            disabled={joinBusy}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={joinBusy || !token.trim()}
            onClick={() => void joinWithToken(token)}
          >
            {joinBusy ? "加入中……" : "加入房间"}
          </button>
        </div>
        {joinError && (
          <p className="online-notice online-notice--error" role="alert">
            {joinError}
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
