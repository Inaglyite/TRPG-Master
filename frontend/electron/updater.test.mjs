import { describe, expect, it, vi } from "vitest";

import {
  createAutoUpdateController,
  initAutoUpdates,
  shouldSkipAutoUpdate,
} from "./updater.cjs";

function createFakeAutoUpdater({ checkError = null } = {}) {
  const handlers = new Map();
  return {
    autoDownload: false,
    autoInstallOnAppQuit: false,
    on: vi.fn((event, handler) => handlers.set(event, handler)),
    emit: (event, payload) => handlers.get(event)?.(payload),
    checkForUpdates: vi.fn(() =>
      checkError ? Promise.reject(checkError) : Promise.resolve(null),
    ),
    quitAndInstall: vi.fn(),
  };
}

function createFakeDialog(choice = 1) {
  return { showMessageBoxSync: vi.fn(() => choice) };
}

describe("自动更新开关", () => {
  it("未打包或显式禁用时跳过", () => {
    expect(shouldSkipAutoUpdate({ isPackaged: false, env: {} })).toBe(true);
    expect(
      shouldSkipAutoUpdate({
        isPackaged: true,
        env: { TRPG_SKIP_AUTO_UPDATE: "1" },
      }),
    ).toBe(true);
    expect(
      shouldSkipAutoUpdate({
        isPackaged: true,
        env: { TRPG_SKIP_AUTO_UPDATE: "true" },
      }),
    ).toBe(true);
    expect(shouldSkipAutoUpdate({ isPackaged: true, env: {} })).toBe(false);
  });

  it("跳过时不会调度任何检查", () => {
    const setTimeoutImpl = vi.fn(() => ({ unref: vi.fn() }));
    const result = initAutoUpdates({
      isPackaged: false,
      env: {},
      setTimeoutImpl,
      autoUpdater: createFakeAutoUpdater(),
      dialog: createFakeDialog(),
    });
    expect(result.started).toBe(false);
    expect(result.controller).toBe(null);
    expect(setTimeoutImpl).not.toHaveBeenCalled();
  });
});

describe("自动更新控制器", () => {
  function startController({ dialogChoice = 1, checkError = null } = {}) {
    const autoUpdater = createFakeAutoUpdater({ checkError });
    const dialog = createFakeDialog(dialogChoice);
    const log = vi.fn();
    const onBeforeQuitAndInstall = vi.fn();
    const timer = { unref: vi.fn() };
    const setTimeoutImpl = vi.fn(() => timer);
    const init = initAutoUpdates({
      isPackaged: true,
      env: {},
      setTimeoutImpl,
      autoUpdater,
      dialog,
      log,
      onBeforeQuitAndInstall,
    });
    expect(init.started).toBe(true);
    expect(setTimeoutImpl).toHaveBeenCalledTimes(1);
    // 触发延迟回调，执行真正的 start()。
    setTimeoutImpl.mock.calls[0][0]();
    return { autoUpdater, dialog, log, onBeforeQuitAndInstall, init };
  }

  it("启动后配置后台下载并发起一次检查", async () => {
    const { autoUpdater } = startController();
    expect(autoUpdater.autoDownload).toBe(true);
    expect(autoUpdater.autoInstallOnAppQuit).toBe(true);
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(1);
    // 重复 start 不重复检查。
    expect(autoUpdater.on).toHaveBeenCalledWith(
      "update-downloaded",
      expect.any(Function),
    );
  });

  it("重复 start 不会重复注册或重复检查", () => {
    const { autoUpdater, init } = startController();
    expect(init.controller.start()).toBe(false);
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(1);
  });

  it("检查失败只记日志不抛出", async () => {
    const { log } = startController({ checkError: new Error("network down") });
    await Promise.resolve();
    await Promise.resolve();
    expect(log).toHaveBeenCalledWith(
      "自动更新请求失败（已忽略）:",
      "network down",
    );
  });

  it("error 事件只记日志", () => {
    const { autoUpdater, log } = startController();
    autoUpdater.emit("error", new Error("github unreachable"));
    expect(log).toHaveBeenCalledWith(
      "自动更新检查失败（已忽略，不影响游戏）:",
      "github unreachable",
    );
  });

  it("下载完成后默认稍后：不重启、退出时自动安装", () => {
    const { autoUpdater, dialog, onBeforeQuitAndInstall } =
      startController({ dialogChoice: 1 });
    autoUpdater.emit("update-downloaded", { version: "1.1.0" });
    expect(dialog.showMessageBoxSync).toHaveBeenCalledTimes(1);
    // 未提供窗口时是单参调用 showMessageBoxSync(options)，取最后一个实参。
    const call = dialog.showMessageBoxSync.mock.calls[0];
    const options = call[call.length - 1];
    expect(options.defaultId).toBe(1);
    expect(autoUpdater.quitAndInstall).not.toHaveBeenCalled();
    expect(onBeforeQuitAndInstall).not.toHaveBeenCalled();
    expect(autoUpdater.autoInstallOnAppQuit).toBe(true);
  });

  it("玩家选择立即重启时先放行退出确认再静默安装", () => {
    const { autoUpdater, onBeforeQuitAndInstall } = startController({
      dialogChoice: 0,
    });
    autoUpdater.emit("update-downloaded", { version: "1.1.0" });
    expect(onBeforeQuitAndInstall).toHaveBeenCalledTimes(1);
    expect(autoUpdater.quitAndInstall).toHaveBeenCalledWith(true, true);
  });
});
