import { useEffect, useRef } from "react";

import {
  checkSession,
  enterLobby,
  enterSoloLobby,
  initOnlineSession,
  resumeLastRoom,
  startGame,
} from "../../../online";
import { connectRoom, disconnectRoom } from "../../../room-ws";
import { useOnlineStore } from "../../../state/online-store";
import { useDelayedClose } from "../transitions";
import { AuthScreen } from "./AuthScreen";
import { LobbyScreen } from "./LobbyScreen";
import { RoomScreen } from "./RoomScreen";
import { SoloCharacterSelectScreen } from "./SoloCharacterSelectScreen";
import { SoloLobbyScreen } from "./SoloLobbyScreen";

/**
 * 联机模式外壳：认证 → 大厅/我的冒险 → 房间（等待页）→ 游戏进行中。
 * 收到权威快照后，starting 与 playing 都不再用覆盖层遮挡：这样开场叙事会
 * 直接流入游戏区，不会在“正在开局”页的背后被玩家错过。输入仍只会在
 * playing 时启用。游戏区顶部的 OnlineRoomDock（由 GameShell 挂载在聊天
 * 面板内）承载进行中房间状态；这里只在 roomOpen 时显示完整管理页。
 *
 * 云端单人（play_mode=solo）走同一 /ws/room 房间引擎，但不经过多人等待页：
 * 连接后自动开局（免 ready/claim），RoomScreen 永不用于 solo 房间。
 */
export function OnlineShell() {
  const view = useOnlineStore((state) => state.view);
  const authStatus = useOnlineStore((state) => state.authStatus);
  const activeWorldId = useOnlineStore((state) => state.activeWorldId);
  const roomStatus = useOnlineStore((state) => state.roomStatus);
  const roomSnapshotReady = useOnlineStore((state) => state.roomSnapshotReady);
  const roomOpen = useOnlineStore((state) => state.roomOpen);
  const roomConnection = useOnlineStore((state) => state.roomConnection);
  const roomMetadata = useOnlineStore((state) => state.roomMetadata);
  const user = useOnlineStore((state) => state.user);
  const members = useOnlineStore((state) => state.members);
  const pendingIntent = useOnlineStore((state) => state.pendingIntent);
  const roomError = useOnlineStore((state) => state.roomError);
  const membersStatus = useOnlineStore((state) => state.membersStatus);

  // solo 房间每个世界只自动开局一次，避免 room_state 重放导致重复 start。
  const soloAutoStartRef = useRef<string | null>(null);

  useEffect(() => {
    const unsubscribe = initOnlineSession();
    // Electron 联机入口的落点意图经 URL 传入（?intent=solo，由主进程拼接）。
    try {
      if (
        new URLSearchParams(window.location.search).get("intent") === "solo"
      ) {
        useOnlineStore.setState({ pendingIntent: "solo" });
      }
    } catch {
      /* URL 解析失败时按多人大厅处理 */
    }
    void (async () => {
      await checkSession();
      // 已有有效 Session（刷新/重启后）直接进入大厅，并尝试回到上次的房间。
      const state = useOnlineStore.getState();
      if (state.authStatus === "authenticated") {
        if (state.pendingIntent === "solo") {
          await enterSoloLobby();
        } else {
          await enterLobby();
          await resumeLastRoom();
        }
      }
    })();
    return unsubscribe;
  }, []);

  // 房间连接属于联机外壳，而不是等待页。等待页与游戏条切换时必须保持
  // 同一个权威 /ws/room；只有离开房间、退出登录或卸载联机模式才断开。
  useEffect(() => {
    if (authStatus !== "authenticated" || view !== "room" || !activeWorldId) {
      return;
    }
    connectRoom(activeWorldId);
    return () => disconnectRoom();
  }, [activeWorldId, authStatus, view]);

  const isSoloRoom = roomMetadata?.play_mode === "solo";
  // 从“我的冒险”进房、元数据尚未加载时也按 solo 流程处理，避免闪现多人等待页。
  const soloRoomFlow =
    view === "room" &&
    (isSoloRoom || (pendingIntent === "solo" && roomMetadata === null));
  const currentMember = members.find((member) => member.user_id === user?.id);
  const soloHasInvestigator = Boolean(currentMember?.investigator);

  // 自动开局只服务“进房时存档已有角色卡”的续玩场景：进房后玩家在角色选择页
  // 里新认领不算——否则点卡预览会立即开局，“以此调查员开始”确认形同虚设。
  // 以成员列表就绪（membersStatus==="ready"）后的首次评估为进房快照。
  const soloEntryRef = useRef<{
    worldId: string;
    hadInvestigator: boolean;
  } | null>(null);
  const membersReady = membersStatus === "ready";
  useEffect(() => {
    if (!isSoloRoom || view !== "room" || !activeWorldId) return;
    if (roomConnection !== "connected" || roomStatus !== "lobby") return;
    if (!membersReady) return;
    if (soloEntryRef.current?.worldId !== activeWorldId) {
      soloEntryRef.current = {
        worldId: activeWorldId,
        hadInvestigator: soloHasInvestigator,
      };
    }
    if (!soloEntryRef.current.hadInvestigator || !soloHasInvestigator) return;
    if (soloAutoStartRef.current === activeWorldId) return;
    soloAutoStartRef.current = activeWorldId;
    void startGame();
  }, [
    activeWorldId,
    isSoloRoom,
    membersReady,
    roomConnection,
    roomStatus,
    soloHasInvestigator,
    view,
  ]);

  // solo 房间没有多人管理页（邀请/移交均不可用）；兜底关掉，永不渲染 RoomScreen。
  useEffect(() => {
    if (isSoloRoom && roomOpen) {
      useOnlineStore.setState({ roomOpen: false });
    }
  }, [isSoloRoom, roomOpen]);

  const playing = view === "room" && roomStatus === "playing";
  // 服务端对开局先标记 starting、再推流，并在 opening 回合完整提交后才切成
  // playing。权威 room_full_state 已经把历史落地时即可展示游戏区；否则玩家会
  // 错过开场的绝大部分叙述。首个快照前仍保持现有连接/房间页，避免空白首屏。
  const openingReady =
    view === "room" && roomStatus === "starting" && roomSnapshotReady;
  const gameSurfaceVisible = playing || openingReady;

  // 进入游戏画面时外壳不立刻卸载：整幕淡出 360ms 揭示游戏区（与本地开始页
  // 的 start-overlay 退场一致）；期间状态回退（如开局被拒）会取消退出。
  const shellOpen = !(gameSurfaceVisible && (!roomOpen || isSoloRoom));
  const shell = useDelayedClose(shellOpen, 360);
  if (!shell.rendered) return null;

  return (
    <div
      className={`online-overlay${shell.closing ? " online-closing" : ""}`}
      data-testid="online-shell"
      aria-hidden={shell.closing || undefined}
    >
      {view === "auth" && <AuthScreen />}
      {view === "lobby" && <LobbyScreen />}
      {view === "solo" && <SoloLobbyScreen />}
      {view === "room" &&
        soloRoomFlow &&
        isSoloRoom &&
        roomStatus === "lobby" && <SoloCharacterSelectScreen />}
      {view === "room" &&
        soloRoomFlow &&
        (!isSoloRoom || roomStatus !== "lobby") && (
          <div
            className="online-start-view solo-start-screen"
            data-testid="solo-start-screen"
          >
            <div className="start-brand">
              <h1 className="online-title online-title--small">
                正在准备你的冒险…
              </h1>
              <p className="online-subtitle" role="status">
                {roomConnection === "connected"
                  ? "守秘人正在布景……"
                  : "正在连接房间……"}
              </p>
            </div>
            {roomError && (
              <p className="online-notice online-notice--error" role="alert">
                {roomError}
              </p>
            )}
            <button
              type="button"
              className="character-back-button solo-start-back"
              onClick={() => void enterSoloLobby()}
            >
              ← 返回我的冒险
            </button>
          </div>
        )}
      {view === "room" && !soloRoomFlow && (
        <RoomScreen
          onClose={
            playing
              ? () => useOnlineStore.setState({ roomOpen: false })
              : undefined
          }
        />
      )}
    </div>
  );
}
