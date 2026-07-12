const { app, BrowserWindow, Menu, dialog } = require("electron");
const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");
const { spawn } = require("node:child_process");

const isDev = process.env.NODE_ENV === "dev";
const devServerUrl = process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173";
const backendUrl = "http://127.0.0.1:8765";
let backendProcess = null;

function log(...args) {
  // electron 主进程日志，启动脚本或终端可见
  console.log("[main]", ...args);
}

// ---- 首次运行：API Key 配置 ----
function ensureEnvJson(backendRoot) {
  const envPath = path.join(backendRoot, ".env.json");
  if (fs.existsSync(envPath)) return true;

  log("未找到 .env.json，弹出配置窗口");

  // 创建一个配置窗口
  const setupWin = new BrowserWindow({
    width: 500,
    height: 480,
    title: "请为您的守秘人注入灵魂",
    resizable: false,
    autoHideMenuBar: true,
    backgroundColor: "#1a0e0a",
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  Menu.setApplicationMenu(null);

  // 内联 HTML 配置表单
  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:system-ui,sans-serif; background:#1a0e0a; color:#d4c5b0; padding:30px; }
  h2 { color:#c9a96e; margin-bottom:8px; font-size:18px; }
  p.sub { color:#8a7e6b; font-size:12px; margin-bottom:20px; }
  label { display:block; margin:12px 0 4px; font-size:13px; color:#b5a48e; }
  input { width:100%; padding:8px 10px; border:1px solid #3a2a1a; background:#0d0805; color:#d4c5b0; border-radius:4px; font-size:13px; }
  .hint { font-size:11px; color:#6b5e4e; margin-top:3px; }
  button { margin-top:18px; padding:10px 24px; background:#5a3a1a; color:#e8d5b0; border:none; border-radius:4px; cursor:pointer; font-size:14px; }
  button:hover { background:#7a4a2a; }
  .error { color:#c44; font-size:12px; margin-top:8px; display:none; }
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
<button onclick="save()">保存并启动</button>
<div class="error" id="err"></div>
<script>
function save() {
  const url = document.getElementById("url").value.trim();
  const key = document.getElementById("key").value.trim();
  if (!key) { document.getElementById("err").style.display="block"; document.getElementById("err").textContent="请填写 API Key"; return; }
  const cfg = { api_key: key, base_url: url || "https://api.deepseek.com" };
  if (document.getElementById("glm-toggle").checked) {
    const glm = document.getElementById("glm").value.trim();
    if (glm) cfg.glm_api_key = glm;
  }
  fetch("http://127.0.0.1:8765/__electron_save_env", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(cfg)
  }).then(r=>r.json()).then(data=>{
    if(data.ok) { window.close(); }
    else { document.getElementById("err").style.display="block"; document.getElementById("err").textContent="保存失败："+data.error; }
  }).catch(e=>{ document.getElementById("err").style.display="block"; document.getElementById("err").textContent="无法连接后端："+e.message; });
}
</script></body></html>`;

  setupWin.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);

  // 启动一个临时 HTTP 服务来接收配置保存请求
  const http = require("node:http");
  const setupServer = http.createServer((req, res) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    if (req.method === "OPTIONS") { res.writeHead(200); res.end(); return; }
    if (req.method === "POST" && req.url === "/__electron_save_env") {
      let body = "";
      req.on("data", d => body += d);
      req.on("end", () => {
        try {
          const cfg = JSON.parse(body);
          fs.writeFileSync(envPath, JSON.stringify(cfg, null, 2), "utf-8");
          log(".env.json 已保存");
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: true }));
        } catch (e) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: false, error: e.message }));
        }
      });
      return;
    }
    res.writeHead(404); res.end();
  });
  setupServer.listen(8765, "127.0.0.1");
  setupWin.on("closed", () => { setupServer.close(); });

  // 等待用户完成配置或关闭窗口
  return new Promise((resolve) => {
    setupWin.on("closed", () => {
      if (fs.existsSync(envPath)) {
        log("配置完成，继续启动");
        resolve(true);
      } else {
        log("用户关闭了配置窗口但未保存");
        resolve(false);
      }
    });
  });
}

function backendExecutablePath() {
  const exe = process.platform === "win32" ? "trpg-server.exe" : "trpg-server";
  return path.join(process.resourcesPath, "backend", exe);
}

function startPackagedBackend() {
  if (!app.isPackaged || process.env.TRPG_EXTERNAL_BACKEND === "1") {
    return Promise.resolve();
  }

  const exePath = backendExecutablePath();
  const backendRoot = path.dirname(exePath);
  const runtimeRoot = path.join(app.getPath("userData"), "runtime");
  fs.mkdirSync(runtimeRoot, { recursive: true });
  log("启动内置后端:", exePath);
  backendProcess = spawn(exePath, [], {
    cwd: backendRoot,
    windowsHide: true,
    env: {
      ...process.env,
      TRPG_PROJECT_ROOT: backendRoot,
      TRPG_RUNTIME_ROOT: runtimeRoot,
    },
  });

  backendProcess.stdout?.on("data", (data) => log("[backend]", String(data).trim()));
  backendProcess.stderr?.on("data", (data) => log("[backend:error]", String(data).trim()));
  backendProcess.on("error", (err) => {
    log("内置后端进程错误:", err.message);
  });
  backendProcess.on("exit", (code, signal) => {
    log("内置后端退出:", code, signal);
    backendProcess = null;
  });

  return waitForBackend();
}

function waitForBackend(timeoutMs = 12000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(`${backendUrl}/api/health`, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });
      req.on("error", retry);
      req.setTimeout(900, () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() - startedAt > timeoutMs) {
        reject(new Error(`后端启动超时：${backendUrl}`));
        return;
      }
      setTimeout(tick, 350);
    };
    tick();
  });
}

function stopBackend() {
  if (!backendProcess) return;
  log("关闭内置后端");
  backendProcess.kill();
  backendProcess = null;
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
    ? win.loadURL(devServerUrl)
    : win.loadFile(path.join(__dirname, "..", "dist", "index.html"));

  loadPromise.catch((err) => {
    log("页面加载失败:", err.message);
    dialog.showErrorBox(
      "启动失败",
      `无法加载游戏界面：\n${err.message}\n\n` +
        (isDev
          ? `请确认 vite dev server 已在 ${devServerUrl} 运行。`
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

app.whenReady().then(async () => {
  log("Electron ready, isDev =", isDev);
  try {
    const backendRoot = app.isPackaged
      ? path.dirname(backendExecutablePath())
      : path.join(__dirname, "..", "..");
    const configured = await ensureEnvJson(backendRoot);
    if (!configured) {
      log("用户未完成配置，退出");
      app.quit();
      return;
    }
    await startPackagedBackend();
  } catch (err) {
    log("内置后端启动失败:", err.message);
    dialog.showErrorBox(
      "守秘人启动失败",
      `无法启动内置后端服务：\n${err.message}\n\n请确认打包资源中包含 backend/trpg-server.exe。`
    );
  }
  createWindow();

  // macOS 重新激活时重建窗口
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

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
