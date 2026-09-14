import { useState } from "react";

import {
  interactionPath,
  structuredUnavailableReason,
} from "../../protocol/structured";
import { narrationGuardReason } from "../../investigator-structured-actions";
import { useAppStore } from "../../state/app-store";
import { useModelStore } from "../../state/model-store";
import { useOnlineStore } from "../../state/online-store";
import { sceneLabel, useSceneStore } from "../../state/scene-store";
import { useStructuredStore } from "../../state/structured-store";
import { MoveDialog } from "./structured/MoveDialog";
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
  const gameStarted = useStartStore((state) => state.gameStarted);
  // 当前场景来自服务端已提交的世界状态（只在回合外应用）：点击“前往”、
  // 正文写到别处、玩家提到地名都不会改这一行；移动被拒绝或取消时保持原位置。
  const sceneStatus = useSceneStore((state) => state.status);
  const sceneName = useSceneStore((state) => state.name);
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
  const openModelSettings = () => openSettings();

  const runPanelCommand = (
    command: "quickSave" | "openSavePanel" | "loadState",
  ) => {
    if (command === "openSavePanel") openSavePanel("manage");
    else if (command === "quickSave") quickSave();
    else loadState();
  };

  const sceneText = sceneLabel({ status: sceneStatus, name: sceneName });
  // 「前往…」只在结构化协议世界出现；不可用时按钮禁用并写明原因。
  const panelPath = useStructuredStore((state) =>
    interactionPath(state.capabilities),
  );
  const submitReady = useAppStore(
    (state) =>
      state.connection === "connected" && !state.dialog && !state.ending,
  );
  const moveBlocked = useStructuredStore((state) => {
    const unavailable = structuredUnavailableReason(
      state.capabilities,
      state.protocolNotice,
    );
    if (unavailable) return unavailable;
    if (!state.capabilities.moveAction) return "服务端未开放移动命令。";
    return narrationGuardReason();
  });
  const sceneMoveAvailable = panelPath === "structured" && gameStarted;
  const [moveOpen, setMoveOpen] = useState(false);

  return (
    <>
      <div className="header-leading">
        <h1>
          <span className="header-candle" aria-hidden="true" />
          {title}
          <span
            id="conn-status"
            className={connection}
            title={connectionTitles[connection]}
          />
        </h1>
        {/* 开局后持续显示“当前已结算位置”。地点名由服务端投影保证是玩家
            可知的公开名称；超长时省略，完整名称放在 title 里。
            结构化模式下右侧提供“前往…”入口：只列服务端公开目的地，
            只有已提交的场景事件会改这一行。 */}
        {gameStarted && (
          <p
            className="header-scene"
            data-scene-status={sceneStatus}
            title={`当前场景 · ${sceneText}`}
            aria-label={`当前场景：${sceneText}`}
          >
            <span className="header-scene-label">
              当前场景 · <span className="header-scene-name">{sceneText}</span>
            </span>
            {sceneMoveAvailable && (
              <button
                type="button"
                className="btn-ghost header-scene-move"
                data-testid="btn-move"
                title={
                  moveBlocked ?? "选择目的地（表示现在出发；抵达不等于调查）"
                }
                disabled={moveBlocked !== null}
                onClick={() => setMoveOpen(true)}
              >
                前往…
              </button>
            )}
          </p>
        )}
        {mode === "online" && <SoloAdventureExitControl />}
      </div>
      {moveOpen && <MoveDialog onClose={() => setMoveOpen(false)} />}
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
