const { app, BrowserWindow, Menu, dialog } = require("electron");
const path = require("node:path");

const isDev = process.env.NODE_ENV === "dev";

function log(...args) {
  // electron 主进程日志，启动脚本或终端可见
  console.log("[main]", ...args);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 860,
    minHeight: 540,
    title: "疯狂宅邸 — TRPG Agent",
    backgroundColor: "#140f0b",
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  Menu.setApplicationMenu(null);

  win.webContents.on("did-finish-load", () => {
    log("页面加载完成:", win.webContents.getURL());
  });
  win.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
    log("页面加载失败事件:", errorCode, errorDescription, validatedURL);
  });
  win.webContents.on("render-process-gone", (_event, details) => {
    log("渲染进程退出:", details.reason, details.exitCode);
  });

  // 加载页面，并在失败时弹窗提示（避免白屏无声失败）
  const loadPromise = isDev
    ? win.loadURL("http://localhost:3000")
    : win.loadFile(path.join(__dirname, "..", "dist", "index.html"));

  loadPromise.catch((err) => {
    log("页面加载失败:", err.message);
    dialog.showErrorBox(
      "启动失败",
      `无法加载游戏界面：\n${err.message}\n\n` +
        (isDev
          ? "请确认 vite dev server 已在 localhost:3000 运行。"
          : "请确认前端已构建（cd frontend && npm run build）。")
    );
  });

  // 打开 DevTools 便于调试（仅 dev）
  if (isDev) win.webContents.openDevTools({ mode: "detach" });

  // 退出确认（仅在用户真的要退时二次确认）
  let confirmedExit = false;
  win.on("close", (e) => {
    if (confirmedExit) return;
    e.preventDefault();
    const choice = dialog.showMessageBoxSync(win, {
      type: "question",
      buttons: ["继续游戏", "退出"],
      defaultId: 0,
      cancelId: 0,
      title: "疯狂宅邸",
      message: "确定要退出吗？",
      detail: "（建议先点 💾 存档；退出后游戏进度不会自动保存）",
    });
    if (choice === 1) {
      confirmedExit = true;
      win.close();
    }
  });
}

app.whenReady().then(() => {
  log("Electron ready, isDev =", isDev);
  createWindow();

  // macOS 重新激活时重建窗口
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  log("window-all-closed, quitting");
  if (process.platform !== "darwin") app.quit();
});

// 捕获未处理异常，避免静默崩溃
process.on("uncaughtException", (err) => {
  log("uncaughtException:", err);
  dialog.showErrorBox("发生错误", err.stack || String(err));
});
