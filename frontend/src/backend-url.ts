const DEFAULT_BACKEND_PORT = "8765";

function configuredOrigin(): string | null {
  const value = import.meta.env.VITE_TRPG_BACKEND_ORIGIN?.trim();
  return value ? value.replace(/\/$/, "") : null;
}

/** 浏览器与 Electron 共用的后端 HTTP origin；生产反代可通过 VITE_TRPG_BACKEND_ORIGIN 覆盖。 */
export function backendHttpOrigin(): string {
  const configured = configuredOrigin();
  if (configured) return configured;
  const protocol = location.protocol === "https:" ? "https:" : "http:";
  const host = location.hostname || "127.0.0.1";
  return `${protocol}//${host}:${DEFAULT_BACKEND_PORT}`;
}

export function backendWebSocketUrl(path = "/ws"): string {
  const origin = new URL(backendHttpOrigin());
  origin.protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  origin.pathname = path;
  return origin.toString();
}
