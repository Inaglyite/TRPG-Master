const {
  app,
  BrowserWindow,
  Menu,
  dialog,
  ipcMain,
  session,
} = require("electron");
const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const { validateCloudOrigin } = require("./cloud-origin.cjs");
const {
  readStoredCloudOrigin,
  writeStoredCloudOrigin,
} = require("./cloud-origin-store.cjs");
const {
  isTrustedSenderUrl,
  isNavigationAllowed,
  isApprovedCloudSenderUrl,
} = require("./ipc-guard.cjs");
const {
  localConfigPath,
  migrateLegacyLocalConfig,
  writeLocalConfig,
} = require("./local-config.cjs");
const {
  isTrpgHealthResponse,
  packagedBackendExecutable,
} = require("./packaged-backend.cjs");
const { pathToFileURL } = require("node:url");

const isDev = process.env.NODE_ENV === "dev";
const devServerUrl = process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173";
const backendUrl = "http://127.0.0.1:8765";
const sourceBackendLauncher =
  process.env.TRPG_SOURCE_BACKEND_LAUNCHER?.trim() || null;
let backendProcess = null;
let backendProcessGroup = false;
// 本地后端改为按需启动：只有用户在模式选择页点了“单机游戏”才会配置/拉起。
let localBackendReady = false;
let localBackendStartPromise = null;
// 联机模式经主进程校验并 loadURL 的云端 origin；导航守卫只放行它。
let approvedCloudOrigin = null;
let mainWindow = null;
let setupWindow = null;
let setupPromise = null;
let pendingSetupConfigPath = null;
// 打包模式下 IPC/导航唯一可信的内置页面 URL（确切的 dist/index.html）。
const trustedFileUrl = pathToFileURL(
  path.join(__dirname, "..", "dist", "index.html"),
).href;
const hasSingleInstanceLock = app.requestSingleInstanceLock();

function log(...args) {
  // electron 主进程日志，启动脚本或终端可见
  console.log("[main]", ...args);
}

// ---- 首次运行：API Key 配置 ----
function ensureEnvJson(envPath) {
  if (fs.existsSync(envPath)) return true;
  if (setupWindow && !setupWindow.isDestroyed() && setupPromise) {
    setupWindow.show();
    setupWindow.focus();
    return setupPromise;
  }

  log("未找到 .env.json，弹出配置窗口");

  const setupWin = new BrowserWindow({
    width: 500,
    height: 480,
    title: "请为您的守秘人注入灵魂",
    resizable: false,
    autoHideMenuBar: true,
    backgroundColor: "#14100c",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, "setup-preload.cjs"),
    },
  });
  setupWindow = setupWin;
  pendingSetupConfigPath = envPath;
  Menu.setApplicationMenu(null);

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:"Noto Serif SC","Songti SC",serif; background:#14100c; color:#ddd0bc; padding:30px; }
  h2 { color:#ecd07a; margin-bottom:8px; font-size:18px; letter-spacing:2px; }
  p.sub { color:#8c7e6a; font-size:12px; margin-bottom:20px; }
  label { display:block; margin:12px 0 4px; font-size:13px; color:#b5a48e; }
  input { width:100%; padding:8px 10px; border:1px solid #3a2f24; background:#0d0a07; color:#ddd0bc; border-radius:4px; font-size:13px; }
  input:focus { outline:none; border-color:#c8a24e; }
  .hint { font-size:11px; color:#5e5346; margin-top:3px; }
  button { margin-top:18px; padding:10px 24px; background:linear-gradient(180deg,#ecd07a,#c8a24e); color:#241806; border:1px solid #8a6e30; border-radius:4px; cursor:pointer; font-size:14px; font-weight:700; letter-spacing:1px; }
  button:hover { filter:brightness(1.08); }
  button:disabled { cursor:wait; opacity:.65; }
  .error { color:#c95050; font-size:12px; margin-top:8px; display:none; }
</style></head><body>
<h2>请为您的守秘人注入灵魂</h2>
<p class="sub">请输入 OpenAI 兼容格式的请求地址及 API Key</p>
<label>请求地址 (Base URL)</label>
<input id="url" value="https://api.deepseek.com">
<div class="hint">默认接入 DeepSeek，可切换为其他 OpenAI 兼容服务</div>
<label>API Key</label>
<input id="key" placeholder="sk-..." autofocus>
<hr style="border:0;border-top:1px solid #2a1a10;margin:18px 0 10px;">
<div style="font-size:11px;color:#6b5e4e;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">可选：接入免费摘要模型以加速长期游戏</div>
<div style="display:flex;align-items:center;margin-top:10px;gap:8px;">
  <input type="checkbox" id="glm-toggle" onchange="document.getElementById('glm-row').style.display=this.checked?'block':'none'" style="width:auto;">
  <label for="glm-toggle" style="margin:0;cursor:pointer;">启用智谱 GLM-4 Flash（免费，用于上下文压缩）</label>
</div>
<div id="glm-row" style="display:none;">
  <label>GLM API Key</label>
  <input id="glm" placeholder="智谱 API Key">
  <div class="hint">注册地址：open.bigmodel.cn → API 密钥（免费额度）</div>
</div>
<button id="save" onclick="save()">保存并启动</button>
<div class="error" id="err"></div>
<script>
async function save() {
  const button = document.getElementById("save");
  const error = document.getElementById("err");
  const url = document.getElementById("url").value.trim();
  const key = document.getElementById("key").value.trim();
  if (!key) { error.style.display="block"; error.textContent="请填写 API Key"; return; }
  const cfg = { api_key: key, base_url: url || "https://api.deepseek.com" };
  if (document.getElementById("glm-toggle").checked) {
    const glm = document.getElementById("glm").value.trim();
    if (glm) cfg.glm_api_key = glm;
  }
  button.disabled = true;
  error.style.display = "none";
  try {
    const result = await window.trpgSetup.saveConfig(cfg);
    if (result && result.ok) { window.close(); return; }
    error.style.display = "block";
    error.textContent = "保存失败：" + (result?.error || "未知错误");
  } catch {
    error.style.display = "block";
    error.textContent = "保存失败：主进程不可用";
  } finally {
    button.disabled = false;
  }
}
</script></body></html>`;

  const setupUrl = `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
  setupWin.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  const guardSetupNavigation = (event, url) => {
    if (url !== setupUrl && url !== "about:blank") event.preventDefault();
  };
  setupWin.webContents.on("will-navigate", guardSetupNavigation);
  setupWin.webContents.on("will-redirect", guardSetupNavigation);
  void setupWin.loadURL(setupUrl).catch((error) => {
    log("配置窗口加载失败:", error.message || String(error));
    if (!setupWin.isDestroyed()) setupWin.close();
  });

  setupPromise = new Promise((resolve) => {
    setupWin.on("closed", () => {
      const configured = fs.existsSync(envPath);
      if (configured) {
        log("配置完成，继续启动");
      } else {
        log("用户关闭了配置窗口但未保存");
      }
      if (setupWindow === setupWin) setupWindow = null;
      if (pendingSetupConfigPath === envPath) pendingSetupConfigPath = null;
      setupPromise = null;
      resolve(configured);
    });
  });
  return setupPromise;
}

function backendExecutablePath() {
  return packagedBackendExecutable(process.resourcesPath, process.platform);
}

async function startManagedBackend({
  command,
  args,
  cwd,
  env,
  label,
  timeoutMs,
  processGroup = false,
}) {
  log(`启动${label}:`, command);
  const child = spawn(command, args, {
    cwd,
    windowsHide: true,
    env,
    // Source setup runs pip/Alembic/import before exec-ing the server. On
    // POSIX, a dedicated process group lets shutdown terminate that entire
    // chain instead of orphaning the current foreground Python child.
    detached: processGroup,
  });
  backendProcess = child;
  backendProcessGroup = processGroup;
  const spawnFailure = new Promise((_, reject) => {
    child.once("error", (error) => {
      reject(new Error(`无法启动${label}：${error.message || error}`));
    });
  });

  child.stdout?.on("data", (data) => log("[backend]", String(data).trim()));
  child.stderr?.on("data", (data) =>
    log("[backend:error]", String(data).trim()),
  );
  child.on("error", (err) => {
    log(`${label}进程错误:`, err.message);
  });
  child.on("exit", (code, signal) => {
    log(`${label}退出:`, code, signal);
    if (backendProcess === child) {
      backendProcess = null;
      backendProcessGroup = false;
    }
    localBackendReady = false;
  });

  try {
    await Promise.race([waitForBackend(timeoutMs, child), spawnFailure]);
  } catch (error) {
    if (child.exitCode === null) signalBackendProcess(child, processGroup);
    throw error;
  }
}

async function startPackagedBackend(exePath, runtimeRoot) {
  const backendRoot = path.dirname(exePath);
  fs.mkdirSync(runtimeRoot, { recursive: true });
  await startManagedBackend({
    command: exePath,
    args: [],
    cwd: backendRoot,
    env: {
      ...process.env,
      // server.py 从这里读取 userData/runtime/.env.json；只读模组资源仍由
      // src.config 在 PyInstaller 的 _internal/ 中自动定位。
      TRPG_PROJECT_ROOT: runtimeRoot,
      TRPG_RUNTIME_ROOT: runtimeRoot,
    },
    label: "内置后端",
    timeoutMs: 30000,
  });
}

async function startSourceBackend(launcherPath) {
  if (!path.isAbsolute(launcherPath)) {
    throw new Error("源码后端启动器必须是绝对路径");
  }
  let resolvedLauncher;
  try {
    resolvedLauncher = fs.realpathSync(launcherPath);
  } catch {
    throw new Error("找不到源码后端启动器，请重新运行 start_desktop.sh");
  }
  if (!fs.statSync(resolvedLauncher).isFile()) {
    throw new Error("源码后端启动器不是普通文件");
  }
  await startManagedBackend({
    command: resolvedLauncher,
    args: ["--backend-only"],
    cwd: path.dirname(resolvedLauncher),
    env: { ...process.env },
    label: "源码后端",
    // 首次安装依赖可能明显慢于正常重启。
    timeoutMs: 180000,
    processGroup: process.platform !== "win32",
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function probeBackendHealth(timeoutMs = 900) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const req = http.get(`${backendUrl}/api/health`, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
        if (body.length > 64 * 1024) {
          req.destroy();
          finish(false);
        }
      });
      res.on("end", () => {
        finish(isTrpgHealthResponse(res.statusCode, body));
      });
    });
    req.once("error", () => finish(false));
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      finish(false);
    });
  });
}

async function waitForBackend(timeoutMs = 12000, expectedChild = null) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    if (expectedChild && expectedChild.exitCode !== null) {
      throw new Error(`内置后端提前退出（状态码 ${expectedChild.exitCode}）`);
    }
    if (await probeBackendHealth()) {
      if (expectedChild) {
        // 避免端口已被旧进程占用时，在新子进程报告 bind 失败前误判成功。
        await delay(250);
        if (expectedChild.exitCode !== null) {
          throw new Error(`内置后端未能占用 ${backendUrl}`);
        }
      }
      return;
    }
    await delay(350);
  }
  throw new Error(`后端启动超时：${backendUrl}`);
}

function signalBackendProcess(child, processGroup, signal = "SIGTERM") {
  try {
    if (processGroup && process.platform !== "win32" && child.pid) {
      process.kill(-child.pid, signal);
      return;
    }
    child.kill(signal);
  } catch (error) {
    if (error?.code !== "ESRCH") {
      log("停止本地后端失败:", error?.message || String(error));
    }
  }
}

function stopBackend() {
  if (!backendProcess) return;
  log("关闭本地后端");
  const child = backendProcess;
  const processGroup = backendProcessGroup;
  signalBackendProcess(child, processGroup);
  const forceTimer = setTimeout(() => {
    if (child.exitCode === null) {
      log("本地后端未及时退出，强制结束");
      signalBackendProcess(child, processGroup, "SIGKILL");
    }
  }, 3000);
  child.once("exit", () => clearTimeout(forceTimer));
  backendProcess = null;
  backendProcessGroup = false;
  localBackendReady = false;
}

// ---- 按需启动本地后端（用户选择单机模式后调用）----
async function ensureLocalBackend() {
  if (localBackendReady) {
    try {
      await waitForBackend(1500);
      return;
    } catch {
      localBackendReady = false;
    }
  }

  if (localBackendStartPromise) {
    await localBackendStartPromise;
    return;
  }

  localBackendStartPromise = (async () => {
    // Reuse an already-running verified local service without taking ownership.
    if (await probeBackendHealth()) return;

    if (process.env.TRPG_EXTERNAL_BACKEND === "1") {
      await waitForBackend();
    } else if (!app.isPackaged && sourceBackendLauncher) {
      await startSourceBackend(sourceBackendLauncher);
    } else if (!app.isPackaged) {
      // Direct `npm run electron` remains a supported development workflow:
      // the developer explicitly owns the separately started backend.
      await waitForBackend();
    } else {
      const exePath = backendExecutablePath();
      const backendRoot = path.dirname(exePath);
      const runtimeRoot = path.join(app.getPath("userData"), "runtime");
      const envPath = localConfigPath(app.getPath("userData"));
      migrateLegacyLocalConfig(path.join(backendRoot, ".env.json"), envPath);
      const configured = await ensureEnvJson(envPath);
      if (!configured) {
        const err = new Error("未完成模型配置");
        err.code = "config-cancelled";
        throw err;
      }
      await startPackagedBackend(exePath, runtimeRoot);
    }
  })();

  try {
    await localBackendStartPromise;
    localBackendReady = true;
  } finally {
    localBackendStartPromise = null;
  }
}

// ---- 导航与新窗口守卫 ----
function navigationAllowed(rawUrl) {
  return isNavigationAllowed(rawUrl, {
    isDev,
    devServerUrl,
    trustedFileUrl,
    approvedCloudOrigin,
  });
}

function trustedSender(senderUrl) {
  return isTrustedSenderUrl(senderUrl, { isDev, devServerUrl, trustedFileUrl });
}

function loadLauncher() {
  if (!mainWindow) return Promise.reject(new Error("窗口尚未就绪"));
  return isDev
    ? mainWindow.loadURL(devServerUrl)
    : mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
}

function registerIpcHandlers() {
  ipcMain.handle("trpg:save-local-config", (event, rawConfig) => {
    const activeWindow = setupWindow;
    const activeWebContents = activeWindow?.webContents;
    if (
      !activeWindow ||
      activeWindow.isDestroyed() ||
      !activeWebContents ||
      event.sender !== activeWebContents ||
      event.senderFrame !== activeWebContents.mainFrame ||
      !pendingSetupConfigPath
    ) {
      log("拒绝来自非配置窗口的本地配置写入");
      return { ok: false, error: "untrusted-sender" };
    }
    const result = writeLocalConfig(pendingSetupConfigPath, rawConfig);
    if (result.ok) log("本地模型配置已保存到 userData/runtime");
    return result;
  });

  ipcMain.handle("trpg:get-online-origin", (event) => {
    if (!trustedSender(event.senderFrame?.url ?? "")) {
      log("拒绝来自不可信页面的 get-online-origin:", event.senderFrame?.url);
      return { ok: false, error: "untrusted-sender" };
    }
    return {
      ok: true,
      origin: readStoredCloudOrigin(app.getPath("userData")),
    };
  });

  ipcMain.handle("trpg:select-local", async (event) => {
    if (!trustedSender(event.senderFrame?.url ?? "")) {
      log("拒绝来自不可信页面的 select-local:", event.senderFrame?.url);
      return { ok: false, error: "untrusted-sender" };
    }
    try {
      await ensureLocalBackend();
      return { ok: true };
    } catch (err) {
      if (err && err.code === "config-cancelled") {
        return { ok: false, cancelled: true };
      }
      const hint =
        process.env.TRPG_EXTERNAL_BACKEND === "1" ||
        (!app.isPackaged && !sourceBackendLauncher)
          ? "（请先单独启动本地后端，或通过 start_desktop.sh 启动桌面版）"
          : "";
      return { ok: false, error: `${err.message || err}${hint}` };
    }
  });

  ipcMain.handle("trpg:select-online", async (event, rawOrigin) => {
    if (!trustedSender(event.senderFrame?.url ?? "")) {
      log("拒绝来自不可信页面的 select-online:", event.senderFrame?.url);
      return { ok: false, error: "untrusted-sender" };
    }
    const origin = validateCloudOrigin(rawOrigin);
    if (!origin) return { ok: false, error: "invalid-origin" };
    if (!mainWindow) return { ok: false, error: "窗口尚未就绪" };
    // 地址由主进程持久化；renderer 随 loadURL 销毁也不会丢失设置。
    const previousOrigin = approvedCloudOrigin;
    const userDataPath = app.getPath("userData");
    const previousStoredOrigin = readStoredCloudOrigin(userDataPath);
    if (!writeStoredCloudOrigin(userDataPath, origin)) {
      return { ok: false, error: "服务器地址保存失败" };
    }
    approvedCloudOrigin = origin;
    try {
      // 同源加载云端页面：认证 Cookie 与 WebSocket 都在该 origin 下工作，
      // 不依赖跨站 Cookie（SameSite=None）。
      await mainWindow.loadURL(`${origin}/?mode=online`);
      // A renderer can return to the launcher after using local mode. Once the
      // cloud page has loaded successfully, an owned local backend is no longer
      // part of this mode and must not keep running in the background.
      stopBackend();
      return { ok: true };
    } catch (err) {
      approvedCloudOrigin = previousOrigin;
      if (!writeStoredCloudOrigin(userDataPath, previousStoredOrigin)) {
        log("恢复上一云端地址失败");
      }
      // 失败时把窗口带回启动器，而不是把 Chromium 错误页留给用户。
      try {
        await loadLauncher();
      } catch (loadErr) {
        log("联机失败后返回启动器失败:", loadErr.message || String(loadErr));
      }
      return { ok: false, error: err.message || String(err) };
    }
  });

  // 云端页面只能请求返回内置启动器，不能直接调用 select-local 启动本机服务。
  ipcMain.handle("trpg:return-launcher", async (event) => {
    const senderUrl = event.senderFrame?.url ?? "";
    if (!isApprovedCloudSenderUrl(senderUrl, approvedCloudOrigin)) {
      log("拒绝来自非当前云端页面的 return-launcher:", senderUrl);
      return { ok: false, error: "untrusted-sender" };
    }
    const previousOrigin = approvedCloudOrigin;
    approvedCloudOrigin = null;
    try {
      await loadLauncher();
      return { ok: true };
    } catch (err) {
      approvedCloudOrigin = previousOrigin;
      return { ok: false, error: err.message || String(err) };
    }
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 860,
    minHeight: 540,
    title: "TRPG Game",
    backgroundColor: "#140f0b",
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, "preload.cjs"),
    },
  });
  mainWindow = win;

  Menu.setApplicationMenu(null);

  // 禁止任意新窗口；导航只允许 file://（单机界面）、dev server 或已校验的云端 origin。
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  const guardNavigation = (event, url) => {
    if (!navigationAllowed(url)) {
      log("阻止导航:", url);
      event.preventDefault();
    }
  };
  win.webContents.on("will-navigate", guardNavigation);
  // HTTP 30x 不一定走 will-navigate；重定向必须使用同一套 origin 白名单。
  win.webContents.on("will-redirect", guardNavigation);
  // 纵深防御：若 Electron/协议边缘路径绕过预导航事件，最终落点仍不能保留。
  win.webContents.on("did-navigate", (_event, url) => {
    if (navigationAllowed(url)) return;
    log("检测到不允许的最终导航，返回启动器:", url);
    approvedCloudOrigin = null;
    void loadLauncher().catch((err) =>
      log("从非法导航恢复启动器失败:", err.message || String(err)),
    );
  });

  win.webContents.on("did-finish-load", () => {
    log("页面加载完成:", win.webContents.getURL());
  });
  win.webContents.on(
    "did-fail-load",
    (_event, errorCode, errorDescription, validatedURL) => {
      log("页面加载失败事件:", errorCode, errorDescription, validatedURL);
    },
  );
  win.webContents.on("render-process-gone", (_event, details) => {
    log("渲染进程退出:", details.reason, details.exitCode);
  });

  // 加载页面，并在失败时弹窗提示（避免白屏无声失败）
  const loadPromise = loadLauncher();

  loadPromise.catch((err) => {
    log("页面加载失败:", err.message);
    dialog.showErrorBox(
      "启动失败",
      `无法加载游戏界面：\n${err.message}\n\n` +
        (isDev
          ? `请确认 vite dev server 已在 ${devServerUrl} 运行。`
          : "请确认前端已构建（cd frontend && npm run build）。"),
    );
  });

  // 打开 DevTools 便于调试（仅 dev）
  if (isDev) win.webContents.openDevTools({ mode: "detach" });

  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
  });

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
      title: "TRPG Game",
      message: "确定要退出吗？",
      detail: "（建议先点 💾 存档；退出后游戏进度不会自动保存）",
    });
    if (choice === 1) {
      confirmedExit = true;
      win.close();
    }
  });
}

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    log("Electron ready, isDev =", isDev);
    // 默认拒绝一切权限请求（摄像头/麦克风/通知等）。
    session.defaultSession.setPermissionRequestHandler(
      (_wc, _permission, callback) => {
        callback(false);
      },
    );
    // 启动即展示模式选择页：不再强制配置本地 API Key 或启动本地后端。
    // 单机后端在用户选择“单机游戏”后按需拉起；联机由 IPC 校验后同源加载。
    registerIpcHandlers();
    createWindow();

    // macOS 重新激活时重建窗口
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
}

app.on("window-all-closed", () => {
  log("window-all-closed, quitting");
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopBackend();
});

// 捕获未处理异常，避免静默崩溃
process.on("uncaughtException", (err) => {
  log("uncaughtException:", err);
  dialog.showErrorBox("发生错误", err.stack || String(err));
});
