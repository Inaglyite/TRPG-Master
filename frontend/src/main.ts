/**
 * main.ts — 入口
 *
 * 主题（theme）由 WS 下发（{type:"theme"}），不再用 fetch('/api/theme')，
 * 因为 Electron 用 file:// 加载页面时 fetch 不可用。
 * 模组下拉框由 WS module_list 事件填充（start.ts 的 populateModuleList）。
 */

import { connect } from "./ws";
import { btnStart } from "./dom";

export { populateModuleList } from "./start";

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
      if (map[k]) document.documentElement.style.setProperty(map[k], v as string);
    });
  }
  if (theme.fonts) {
    if (theme.fonts.body) document.documentElement.style.setProperty("--font", theme.fonts.body);
    if (theme.fonts.mono) document.documentElement.style.setProperty("--font-mono", theme.fonts.mono);
  }
  if (theme.title) {
    document.title = theme.title;
    const h1 = document.querySelector("#header h1");
    if (h1) h1.innerHTML = `🏛 ${theme.title}<span id="conn-status" class="connecting"></span>`;
    const el = document.getElementById("start-title");
    if (el) el.textContent = `🏛 ${theme.title}`;
  }
  const sub = document.getElementById("start-subtitle");
  if (sub && theme.subtitle) sub.textContent = theme.subtitle;
  if (btnStart && theme.startButtonText) btnStart.textContent = theme.startButtonText;
}

// 启动：先连接 WS，主题/模组列表/存档列表都由 WS 下发
connect();
