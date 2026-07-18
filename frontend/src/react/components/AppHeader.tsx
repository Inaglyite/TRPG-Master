import { useAppStore } from "../../state/app-store";
import { useModelStore } from "../../state/model-store";

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
        <button
          id="btn-new"
          title="新游戏"
          aria-label="开始新游戏"
          onClick={() => location.reload()}
        >
          🆕
        </button>
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
        <button
          id="btn-model-settings"
          title="模型设置"
          aria-label="打开模型设置"
          onClick={openModelSettings}
        />
      </div>
    </>
  );
}
import { loadState, openSavePanel, quickSave } from "../../panels";
import { openSettings } from "../../settings";
