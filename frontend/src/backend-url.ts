const DEFAULT_BACKEND_PORT = "8765";

function configuredOrigin(): string | null {
  const value = import.meta.env.VITE_TRPG_BACKEND_ORIGIN?.trim();
  return value ? value.replace(/\/$/, "") : null;
}

type BrowserLocation = Pick<Location, "protocol" | "hostname" | "origin">;

/**
 * 生产页面由 FastAPI/Nginx 同源托管，必须沿用页面 origin（包括 443/8443
 * 等实际端口），才能复用安全 Cookie 与 WSS。Vite 开发页才直连 8765。
 */
export function defaultBackendHttpOrigin(
  browserLocation: BrowserLocation,
  development: boolean,
): string {
  if (
    !development &&
    (browserLocation.protocol === "https:" ||
      browserLocation.protocol === "http:")
  ) {
    return browserLocation.origin;
  }
  const protocol = browserLocation.protocol === "https:" ? "https:" : "http:";
  const host = browserLocation.hostname || "127.0.0.1";
  return `${protocol}//${host}:${DEFAULT_BACKEND_PORT}`;
}

/** 浏览器与 Electron 共用的后端 HTTP origin；生产反代可通过 VITE_TRPG_BACKEND_ORIGIN 覆盖。 */
export function backendHttpOrigin(): string {
  const configured = configuredOrigin();
  if (configured) return configured;
  return defaultBackendHttpOrigin(location, import.meta.env.DEV);
}

export function backendWebSocketUrl(path = "/ws"): string {
  const origin = new URL(backendHttpOrigin());
  origin.protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  origin.pathname = path;
  return origin.toString();
}
