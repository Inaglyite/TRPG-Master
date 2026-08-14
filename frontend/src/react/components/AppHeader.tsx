import { useAppStore } from "../../state/app-store";
import { useModelStore } from "../../state/model-store";
import { useOnlineStore } from "../../state/online-store";
import { returnToStartMenu } from "../../start";
import { useStartStore } from "../../state/start-store";
import { SoloAdventureExitControl } from "./online/SoloAdventureExitControl";

const connectionTitles = {
  connected: "已连接到守秘人",
  connecting: "连接中…",
  disconnected: "连接已断开，正在重试",
} as const;

export function AppHeader() {
  const connection = useAppStore((state) => state.connection);
  const title = useAppStore((state) => state.title);
  const openNotes = useAppStore((state) => state.setUtilityOpen);
  const characterPanelOpen = useAppStore((state) => state.characterPanelOpen);
  const setCharacterPanelOpen = useAppStore(
    (state) => state.setCharacterPanelOpen,
  );
  const quickSaveState = useAppStore((state) => state.quickSaveState);
  const mode = useAppStore((state) => state.mode);
  const gameStarting = useStartStore((state) => state.gameStarting);
  // 多人房间中存档/读档为房主专属操作（服务端按 Session 再校验）；
  // selector 订阅成员/用户变化，房主移交后 UI 即时更新。
  const isOwner = useOnlineStore((state) => {
    const uid = state.user?.id;
    return (
      uid != null &&
      state.members.some(
        (member) => member.user_id === uid && member.role === "owner",
      )
    );
  });
  const saveOpsVisible = mode !== "online" || isOwner;
  const openModelSettings = () => {
    useModelStore.setState((state) => ({
      open: true,
      narrativeDraft: state.narrativeModel,
      judgementDraft: state.judgementModel,
    }));
    openSettings();
  };

  const runPanelCommand = (
    command: "quickSave" | "openSavePanel" | "loadState",
  ) => {
    if (command === "openSavePanel") openSavePanel("manage");
    else if (command === "quickSave") quickSave();
    else loadState();
  };

  return (
    <>
      <h1>
        <span className="header-candle" aria-hidden="true" />
        {title}
        <span
          id="conn-status"
          className={connection}
          title={connectionTitles[connection]}
        />
      </h1>
      <div id="toolbar">
        {saveOpsVisible && (
          <>
            <button
              id="btn-save"
              className={
                quickSaveState === "idle"
                  ? ""
                  : quickSaveState === "saving"
                    ? "saving"
                    : quickSaveState === "success"
                      ? "save-success"
                      : "save-failed"
              }
              disabled={quickSaveState === "saving"}
              title={
                quickSaveState === "saving"
                  ? "保存中…"
                  : quickSaveState === "success"
                    ? "已保存"
                    : quickSaveState === "failed"
                      ? "保存失败"
                      : "快速存档"
              }
              aria-label="快速存档"
              onClick={() => void runPanelCommand("quickSave")}
            >
              💾
            </button>
            <button
              id="btn-load"
              title="存档管理"
              aria-label="打开存档管理"
              onClick={() => void runPanelCommand("openSavePanel")}
            >
              📂
            </button>
          </>
        )}
        {mode === "local" && (
          <button
            id="btn-new"
            title={gameStarting ? "正在开始新游戏…" : "返回开局选择"}
            aria-label="开始新游戏"
            disabled={gameStarting}
            onClick={returnToStartMenu}
          >
            🆕
          </button>
        )}
        {mode === "online" && <SoloAdventureExitControl />}
        <button
          id="btn-panel"
          title="角色/线索"
          aria-label="打开角色和线索面板"
          onClick={() => {
            const open = !characterPanelOpen;
            setCharacterPanelOpen(open);
            if (open) void runPanelCommand("loadState");
          }}
        >
          📋
        </button>
        <button
          id="btn-notes"
          title="调查笔记"
          aria-label="打开调查笔记"
          onClick={() => openNotes(true)}
        />
        {mode !== "online" && (
          <button
            id="btn-model-settings"
            title="模型设置"
            aria-label="打开模型设置"
            onClick={openModelSettings}
          />
        )}
      </div>
    </>
  );
}
import { loadState, openSavePanel, quickSave } from "../../panels";
import { openSettings } from "../../settings";
