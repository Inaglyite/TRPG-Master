/**
 * main.ts — 入口
 *
 * 主题（theme）由 WS 下发（{type:"theme"}），不再用 fetch('/api/theme')，
 * 因为 Electron 用 file:// 加载页面时 fetch 不可用。
 * 模组下拉框由 WS module_list 事件填充（start.ts 的 populateModuleList）。
 */

import { connect } from "./ws";
import { getConnState, setConn } from "./dom";
import "./module-import";
import "./settings";

export { populateModuleList } from "./start";

function isSafeThemeColor(value: unknown): value is string {
  return typeof value === "string" && value.length <= 80 && CSS.supports("color", value);
}

function isSafeFontFamily(value: unknown): value is string {
  return typeof value === "string"
    && value.length <= 240
    && !/[;{}<>]/.test(value)
    && !/url\s*\(/i.test(value);
}

// ---- 应用主题（由 WS theme 消息触发）----
export function applyTheme(theme: any) {
  if (!theme) return;
  if (theme.colors) {
    const map: Record<string, string> = {
      bg: "--bg", bg2: "--bg2", bg3: "--bg3", bg4: "--bg4",
      text: "--text", textDim: "--text-dim", textFaint: "--text-faint",
      gold: "--gold", goldDim: "--gold-dim", goldBright: "--gold-bright",
      red: "--red", redDim: "--red-dim", redBright: "--red-bright",
      crimson: "--crimson", crimsonBright: "--crimson-bright",
      green: "--green", blue: "--blue", blueBright: "--blue-bright",
      purple: "--purple", ink: "--ink",
      border: "--border", borderBright: "--border-bright",
    };
    Object.entries(theme.colors).forEach(([k, v]) => {
      if (map[k] && isSafeThemeColor(v)) {
        document.documentElement.style.setProperty(map[k], v);
      }
    });
  }
  if (theme.fonts) {
    if (isSafeFontFamily(theme.fonts.body)) {
      document.documentElement.style.setProperty("--font", theme.fonts.body);
    }
    if (isSafeFontFamily(theme.fonts.mono)) {
      document.documentElement.style.setProperty("--font-mono", theme.fonts.mono);
    }
  }
  if (theme.title) {
    document.title = theme.title;
    const h1 = document.querySelector("#header h1");
    if (h1) {
      h1.textContent = `🏛 ${theme.title}`;
      const status = document.createElement("span");
      status.id = "conn-status";
      h1.appendChild(status);
      setConn(getConnState());
    }
    const el = document.getElementById("start-title");
    if (el) el.textContent = theme.title;
  }
  const sub = document.getElementById("start-subtitle");
  if (sub && theme.subtitle) sub.textContent = theme.subtitle;
}

// 启动：先连接 WS，主题/模组列表/存档列表都由 WS 下发
connect();
