/**
 * Electron preload 白名单桥（electron/preload.cjs 注入 window.trpgDesktop）。
 * 浏览器环境下不存在，返回 null——调用方按非 Electron 流程降级。
 */
export type DesktopResult = {
  ok: boolean;
  error?: string;
  cancelled?: boolean;
};

export type DesktopOriginResult = DesktopResult & {
  origin?: string | null;
};

export type DesktopBridge = {
  getOnlineOrigin(): Promise<DesktopOriginResult>;
  selectLocalMode(): Promise<DesktopResult>;
  selectOnlineMode(
    origin: string,
    intent?: "lobby" | "solo",
  ): Promise<DesktopResult>;
  returnToLauncher(): Promise<DesktopResult>;
};

declare global {
  interface Window {
    trpgDesktop?: DesktopBridge;
  }
}

export function desktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  return window.trpgDesktop ?? null;
}
