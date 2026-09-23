import { useEffect, useState } from "react";

import {
  getCloudOrigin,
  normalizeOrigin,
  OFFICIAL_CLOUD_ORIGIN,
} from "../../api/client";
import { desktopBridge } from "../../desktop";
import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import gmUrl from "../../assets/ui/gm.webp";
import gmDiceUrl from "../../assets/ui/gm_dice.webp";
import gmLittleUrl from "../../assets/ui/gm_little.webp";
import gmThinkingUrl from "../../assets/ui/gm_thinking.webp";

type ModePreview = "local" | "solo" | "online";

/** 守秘人迎宾：默认 Q 版迎宾，悬停/聚焦各模式时换姿态（本地资产，非模组内容）。 */
const COMPANION_POSE: Record<ModePreview | "default", string> = {
  default: gmLittleUrl,
  local: gmUrl,
  solo: gmThinkingUrl,
  online: gmDiceUrl,
};
const COMPANION_LINE: Record<ModePreview | "default", string> = {
  default: "请选择你的模式",
  local: "在你的本地进行游戏，不进行云端存档的上传与登录",
  solo: "登录云端账号，你的冒险保存在云端，换一台设备也能继续。",
  online: "创建或加入房间，和朋友一起调查同一份档案。",
};

/**
 * 启动后的第一个界面：本地单人（桌面版本地后端）、云端单人（账号 + 私密世界）
 * 或多人游戏（云端账号）。Electron 中联机两种入口都经主进程完成：主进程校验
 * https origin 后同源加载云端页面（认证 Cookie/WS 同源，不靠跨站 Cookie）。
 */
export function ModeSelectScreen() {
  const title = useAppStore((state) => state.title);
  const subtitle = useAppStore((state) => state.subtitle);
  const setMode = useAppStore((state) => state.setMode);

  const [busyMode, setBusyMode] = useState<"local" | "solo" | "online" | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  // 默认官方服务器；自定义 origin 仅开发/验收用，普通流程折叠不展示。
  const [originDraft, setOriginDraft] = useState(
    getCloudOrigin() ?? OFFICIAL_CLOUD_ORIGIN,
  );
  const [originConfigOpen, setOriginConfigOpen] = useState(false);
  // 守秘人迎宾：悬停/聚焦模式卡时切换姿态与气泡解说（触屏点按直接进入，
  // 不影响既有交互；装饰性内容整体 aria-hidden）。
  const [preview, setPreview] = useState<ModePreview | null>(null);
  const previewOf = (mode: ModePreview) => ({
    onMouseEnter: () => setPreview(mode),
    onMouseLeave: () => setPreview(null),
    onFocus: () => setPreview(mode),
    onBlur: () => setPreview(null),
  });
  const companionKey = preview ?? "default";

  const bridge = desktopBridge();

  useEffect(() => {
    if (!bridge) return;
    let active = true;
    void bridge.getOnlineOrigin().then((result) => {
      if (active && result.ok && result.origin) {
        setOriginDraft(result.origin);
        // 已保存的自定义 origin 直接展开，让用户看到自己不在官方服。
        if (result.origin !== OFFICIAL_CLOUD_ORIGIN) setOriginConfigOpen(true);
      }
    });
    return () => {
      active = false;
    };
  }, [bridge]);

  async function chooseLocal() {
    // 纯浏览器没有本地后端可连：本地单人只桌面版可用，按钮已禁用。
    if (!bridge) return;
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

  /** 联机入口公共部分：浏览器直接切 online 模式；Electron 由主进程同源加载云端页。 */
  async function connectCloud(intent: "lobby" | "solo") {
    if (!bridge) {
      useOnlineStore.setState({ pendingIntent: intent });
      setMode("online");
      return;
    }
    const trimmed = originDraft.trim();
    // 留空即官方服务器；只有显式填写时才校验自定义地址。
    const normalized = trimmed
      ? normalizeOrigin(trimmed)
      : OFFICIAL_CLOUD_ORIGIN;
    if (!normalized || !normalized.startsWith("https://")) {
      setError("请输入云端的 https 服务器地址，例如 https://trpg.example.com");
      return;
    }
    setBusyMode(intent === "solo" ? "solo" : "online");
    setError(null);
    const result = await bridge.selectOnlineMode(normalized, intent);
    setBusyMode(null);
    if (result.ok) {
      // 主进程已经持久化地址并同源加载云端页面（intent 经 URL 传递）。
      return;
    }
    setError(
      result.error === "invalid-origin"
        ? "服务器地址无效：只允许裸 https origin（无路径与参数）"
        : (result.error ?? "无法连接服务器"),
    );
  }

  async function chooseSolo() {
    await connectCloud("solo");
  }

  async function chooseOnline() {
    await connectCloud("lobby");
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
            disabled={busyMode !== null || !bridge}
            title={bridge ? undefined : "本地单人请使用桌面版"}
            onClick={() => void chooseLocal()}
            {...previewOf("local")}
          >
            <span className="start-art-label mode-card-title">
              {busyMode === "local" ? "正在启动…" : "本地单人"}
            </span>
            <span className="mode-card-desc">
              {bridge
                ? "连接本地服务 · 使用本地存档 · 无需账号"
                : "本地单人请使用桌面版"}
            </span>
          </button>
          <button
            type="button"
            className="start-art-button mode-card"
            disabled={busyMode !== null}
            onClick={() => void chooseSolo()}
            {...previewOf("solo")}
          >
            <span className="start-art-label mode-card-title">
              {busyMode === "solo" ? "正在连接…" : "云端单人"}
            </span>
            <span className="mode-card-desc">
              登录云端账号 · 私密世界 · 随时随地继续冒险
            </span>
          </button>
          <button
            type="button"
            className="start-art-button mode-card"
            disabled={busyMode !== null}
            onClick={() => void chooseOnline()}
            {...previewOf("online")}
          >
            <span className="start-art-label mode-card-title">
              {busyMode === "online" ? "正在连接…" : "多人游戏"}
            </span>
            <span className="mode-card-desc">
              登录云端账号 · 创建或加入房间 · 与朋友同游
            </span>
          </button>
        </div>
        {bridge && !originConfigOpen && (
          <button
            type="button"
            className="btn-ghost mode-select-origin-toggle"
            disabled={busyMode !== null}
            onClick={() => setOriginConfigOpen(true)}
          >
            自定义服务器（开发/验收）
          </button>
        )}
        {bridge && originConfigOpen && (
          <div className="mode-select-origin">
            <input
              value={originDraft}
              onChange={(event) => setOriginDraft(event.target.value)}
              placeholder={`留空使用官方服务器 ${OFFICIAL_CLOUD_ORIGIN}`}
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
      {/* 守秘人迎宾区（≥1024px 显示）：气泡解说随悬停/聚焦模式切换，
          姿态图随之更换；信息在模式卡副标题里已有，整体装饰性 aria-hidden。 */}
      <div className="mode-select-companion" aria-hidden="true">
        <div className="mode-companion-bubble">
          <span className="mode-companion-text" key={companionKey}>
            {COMPANION_LINE[companionKey]}
          </span>
        </div>
        <img
          className="gm-mascot mode-companion-mascot"
          key={companionKey}
          src={COMPANION_POSE[companionKey]}
          alt=""
        />
      </div>
    </div>
  );
}
