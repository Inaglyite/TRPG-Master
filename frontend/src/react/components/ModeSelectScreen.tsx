import { useEffect, useState } from "react";

import { getCloudOrigin, normalizeOrigin } from "../../api/client";
import { desktopBridge } from "../../desktop";
import { useAppStore } from "../../state/app-store";

/**
 * 启动后的第一个界面：选择单机（本地后端、匿名游玩）或多人（云端账号）。
 * Electron 中两种模式都经主进程完成：单机按需拉起本地后端，联机由主进程
 * 校验 https origin 后同源加载云端页面（认证 Cookie/WS 同源，不靠跨站 Cookie）。
 */
export function ModeSelectScreen() {
  const title = useAppStore((state) => state.title);
  const subtitle = useAppStore((state) => state.subtitle);
  const setMode = useAppStore((state) => state.setMode);

  const [busyMode, setBusyMode] = useState<"local" | "online" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [originDraft, setOriginDraft] = useState(getCloudOrigin() ?? "");

  const bridge = desktopBridge();

  useEffect(() => {
    if (!bridge) return;
    let active = true;
    void bridge.getOnlineOrigin().then((result) => {
      if (active && result.ok && result.origin) {
        setOriginDraft(result.origin);
      }
    });
    return () => {
      active = false;
    };
  }, [bridge]);

  async function chooseLocal() {
    if (!bridge) {
      setMode("local");
      return;
    }
    setBusyMode("local");
    setError(null);
    const result = await bridge.selectLocalMode();
    setBusyMode(null);
    if (result.ok) {
      setMode("local");
    } else if (!result.cancelled) {
      setError(result.error ?? "本地后端启动失败");
    }
  }

  async function chooseOnline() {
    if (!bridge) {
      setMode("online");
      return;
    }
    const normalized = normalizeOrigin(originDraft);
    if (!normalized || !normalized.startsWith("https://")) {
      setError("请输入云端的 https 服务器地址，例如 https://trpg.example.com");
      return;
    }
    setBusyMode("online");
    setError(null);
    const result = await bridge.selectOnlineMode(normalized);
    setBusyMode(null);
    if (result.ok) {
      // 主进程已经持久化地址并同源加载云端页面。
      return;
    }
    setError(
      result.error === "invalid-origin"
        ? "服务器地址无效：只允许裸 https origin（无路径与参数）"
        : (result.error ?? "无法连接服务器"),
    );
  }

  return (
    <div className="online-overlay" data-testid="mode-select">
      <div className="online-box mode-select-box">
        <div className="start-brand">
          <h1 className="online-title">{title}</h1>
          <p className="online-subtitle">{subtitle}</p>
        </div>
        <p className="mode-select-hint">选择本次的游戏方式</p>
        <div className="mode-select-actions">
          <button
            type="button"
            className="start-art-button mode-card"
            disabled={busyMode !== null}
            onClick={() => void chooseLocal()}
          >
            <span className="start-art-label mode-card-title">
              {busyMode === "local" ? "正在启动…" : "单机游戏"}
            </span>
            <span className="mode-card-desc">
              连接本地服务 · 使用本地存档 · 无需账号
            </span>
          </button>
          <button
            type="button"
            className="start-art-button mode-card"
            disabled={busyMode !== null}
            onClick={() => void chooseOnline()}
          >
            <span className="start-art-label mode-card-title">
              {busyMode === "online" ? "正在连接…" : "多人游戏"}
            </span>
            <span className="mode-card-desc">
              登录云端账号 · 创建或加入房间 · 与朋友同游
            </span>
          </button>
        </div>
        {bridge && (
          <div className="mode-select-origin">
            <input
              value={originDraft}
              onChange={(event) => setOriginDraft(event.target.value)}
              placeholder="云端服务器：https://trpg.example.com"
              aria-label="云端服务器地址"
              disabled={busyMode !== null}
            />
          </div>
        )}
        {error && (
          <p className="online-notice online-notice--error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
