/**
 * start.ts — 开局与 GM 回合流程
 *
 * 管理游戏全局状态：gameStarted（是否已开始）、gameStarting（开场回合是否进行中）。
 * 负责：onGmTurnStart()、resetStartButton()、startGame()、continueGame()、
 *       onSaveAvailable()、onSaveList()，以及开始界面提示更新。
 * 同时绑定开始 / 继续 / 新游戏按钮事件。
 */

import { startOverlay, btnStart, btnContinue, btnNew } from "./dom";
import { safeSend } from "./ws";
import { addMsg, showGmThinking } from "./renderer";
import { openSavePanel, renderSavePanel } from "./panels";
import { enableInput } from "./options";

// ---- 全局游戏状态 ----
export let gameStarted = false; // 游戏是否已开始（隐藏开场遮罩用）
export let gameStarting = false; // 开场回合是否进行中（防重复点击）

export function getGameStarted(): boolean {
  return gameStarted;
}

export function getGameStarting(): boolean {
  return gameStarting;
}

// ---- GM 回合开始 ----
export function onGmTurnStart() {
  // 服务端开始跑 GM 回合：隐藏开场遮罩，进入"等待叙述"状态
  if (!gameStarted) {
    gameStarted = true;
    startOverlay.classList.add("hidden");
  }
  // GM 回合进行中：禁用输入，提示玩家等待，显示思考动画
  enableInput(false);
  showGmThinking();
}

// ---- 重置开始按钮状态（连接错误等场景） ----
export function resetStartButton() {
  gameStarting = false;
  btnStart.disabled = false;
  btnContinue.disabled = false;
  btnStart.textContent = btnContinue.classList.contains("hidden")
    ? "🕯 点燃烛火，开始故事"
    : "🕯 开始新游戏";
  btnContinue.textContent = "📜 继续游戏";
}

// ---- 开始新游戏 ----
export function startGame() {
  if (gameStarting) return;
  gameStarting = true;
  btnStart.disabled = true;
  btnContinue.disabled = true;
  btnStart.textContent = "守秘人正在布景……";
  safeSend(JSON.stringify({ type: "start" }));
}

// ---- 继续游戏 ----
export function continueGame() {
  // 打开存档面板让玩家选——不直接加载最新档
  openSavePanel();
}

// ---- 服务端通知有存档可用 ----
export function onSaveAvailable(data: any) {
  if (data.has_save) {
    btnContinue.classList.remove("hidden");
    btnStart.textContent = "🕯 开始新游戏";
  }
}

// ---- 收到存档列表 ----
export function onSaveList(data: any) {
  const saves = data.saves || [];
  const hint = document.getElementById("start-hint")!;

  // 开始界面：只显示最近存档摘要 + 继续游戏按钮
  if (saves.length > 0) {
    btnContinue.classList.remove("hidden");
    btnStart.textContent = "🕯 开始新游戏";
    const latest = saves[0];
    hint.textContent =
      `最近存档: ${latest.scene_name || "?"} | HP ${latest.hp || "?"} SAN ${latest.san || "?"} | ${latest.clue_count || 0} 条线索`;
  } else {
    hint.textContent = "还未有存档。开始游戏后进度将自动保存。";
  }

  // 游戏内的存档管理面板（💾按钮）始终同步
  renderSavePanel(saves);
}

// ==================== 按钮事件绑定 ====================

btnStart.onclick = startGame;
btnContinue.onclick = continueGame;
btnNew.onclick = () => location.reload();
