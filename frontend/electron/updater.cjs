"use strict";

// 自动更新：仅在打包版启用，经 GitHub Releases 的 latest.yml 检查新版本。
// 任何检查/下载失败都只记日志（fail-open），绝不影响游戏本体。
// 验收/测试环境可用 TRPG_SKIP_AUTO_UPDATE=1 关闭。

const DEFAULT_CHECK_DELAY_MS = 10000;

function shouldSkipAutoUpdate({ isPackaged, env }) {
  if (!isPackaged) return true;
  const flag = env?.TRPG_SKIP_AUTO_UPDATE;
  return flag === "1" || flag === "true";
}

function createAutoUpdateController({
  autoUpdater,
  dialog,
  getWindow,
  log = () => {},
  onBeforeQuitAndInstall,
}) {
  let started = false;
  let downloadedVersion = null;

  function promptRestart(info) {
    downloadedVersion = info?.version || null;
    log(`新版本 ${downloadedVersion || "unknown"} 已下载完成`);
    const options = {
      type: "info",
      buttons: ["立即重启更新", "稍后"],
      // 默认“稍后”：玩家正在游戏时回车不会打断会话；退出时仍会静默安装。
      defaultId: 1,
      cancelId: 1,
      title: "发现新版本",
      message: `新版本${downloadedVersion ? ` v${downloadedVersion}` : ""}已下载完成`,
      detail: "选择“立即重启更新”马上完成安装；选择“稍后”会在退出游戏时自动安装。",
    };
    const win = getWindow?.() || null;
    const choice =
      win && !win.isDestroyed?.()
        ? dialog.showMessageBoxSync(win, options)
        : dialog.showMessageBoxSync(options);
    if (choice === 0) {
      onBeforeQuitAndInstall?.();
      // 静默安装并在结束后重启；NSIS per-user 安装支持静默更新。
      autoUpdater.quitAndInstall(true, true);
    }
  }

  function start() {
    if (started) return false;
    started = true;
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.on("checking-for-update", () => log("正在检查更新…"));
    autoUpdater.on("update-available", (info) =>
      log(`发现新版本 ${info?.version || "unknown"}，后台下载中`),
    );
    autoUpdater.on("update-not-available", () => log("当前已是最新版本"));
    autoUpdater.on("error", (err) =>
      log("自动更新检查失败（已忽略，不影响游戏）:", err?.message || String(err)),
    );
    autoUpdater.on("update-downloaded", promptRestart);
    Promise.resolve(autoUpdater.checkForUpdates()).catch((err) =>
      log("自动更新请求失败（已忽略）:", err?.message || String(err)),
    );
    return true;
  }

  return {
    start,
    getDownloadedVersion: () => downloadedVersion,
    isStarted: () => started,
  };
}

function initAutoUpdates({
  isPackaged,
  env,
  checkDelayMs = DEFAULT_CHECK_DELAY_MS,
  setTimeoutImpl = setTimeout,
  log = () => {},
  ...controllerDeps
}) {
  if (shouldSkipAutoUpdate({ isPackaged, env })) {
    log("自动更新未启用（开发模式或已禁用）");
    return { started: false, controller: null };
  }
  const controller = createAutoUpdateController({ log, ...controllerDeps });
  // 延迟到启动完成后再检查，避免与首屏资源加载抢占网络。
  const timer = setTimeoutImpl(() => controller.start(), checkDelayMs);
  timer?.unref?.();
  return { started: true, controller };
}

module.exports = {
  DEFAULT_CHECK_DELAY_MS,
  createAutoUpdateController,
  initAutoUpdates,
  shouldSkipAutoUpdate,
};
