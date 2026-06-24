/**
 * main.ts — 入口
 *
 * 模块下拉框由 WS module_list 事件填充（start.ts 中的 populateModuleList），
 * 此处仅负责 theme 加载和启动。
 */

import { connect } from "./ws";
import { btnStart } from "./dom";

export { populateModuleList } from "./start";

// ---- 加载主题 ----
async function loadTheme() {
  try {
    const resp = await fetch("/api/theme");
    const theme = await resp.json();
    if (theme.colors) {
      const map: Record<string, string> = {
        bg: "--bg", bg2: "--bg2", bg3: "--bg3", bg4: "--bg4",
        text: "--text", textDim: "--text-dim", textFaint: "--text-faint",
        gold: "--gold", goldDim: "--gold-dim", goldBright: "--gold-bright",
        red: "--red", redBright: "--red-bright",
        green: "--green", blue: "--blue", blueBright: "--blue-bright",
        purple: "--purple", border: "--border", borderBright: "--border-bright",
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
      if (h1) h1.innerHTML = `🏛 ${theme.title}<span id=\"conn-status\" class=\"connecting\"></span>`;
      const el = document.getElementById("start-title");
      if (el) el.textContent = `🏛 ${theme.title}`;
    }
    const sub = document.getElementById("start-subtitle");
    if (sub && theme.subtitle) sub.textContent = theme.subtitle;
    if (btnStart && theme.startButtonText) btnStart.textContent = theme.startButtonText;
  } catch { /* ignore */ }
}

// 兜底
setTimeout(() => {
  const sel = document.getElementById("module-select") as HTMLSelectElement;
  if (sel && sel.options.length === 0) {
    const o = document.createElement("option"); o.textContent = "疯狂宅邸"; sel.appendChild(o);
  }
}, 3000);

loadTheme().then(() => connect());
