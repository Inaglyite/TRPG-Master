/**
 * main.ts — 入口
 *
 * 职责：加载模块列表 → 加载主题 → 连接
 */

import { connect } from "./ws";
import { btnStart } from "./dom";

let activeModule = "";

// ---- 加载模块列表 ----
async function loadModules() {
  try {
    const resp = await fetch("/api/modules");
    const data = await resp.json();
    const sel = document.getElementById("module-select") as HTMLSelectElement;
    if (!sel) return;

    sel.innerHTML = "";
    data.modules.forEach((m: any) => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.title;
      if (m.id === data.active) { opt.selected = true; activeModule = m.id; }
      sel.appendChild(opt);
    });

    sel.onchange = async () => {
      const chosen = sel.value;
      if (chosen === activeModule) return;
      const resp = await fetch("/api/modules/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ module: chosen }),
      });
      const result = await resp.json();
      if (result.ok) {
        activeModule = chosen;
        await loadTheme();
      }
    };
  } catch (e) {
    console.error("loadModules failed:", e);
    // 列表加载失败：填充默认选项
    const sel = document.getElementById("module-select") as HTMLSelectElement;
    if (sel && sel.options.length === 0) {
      const opt = document.createElement("option");
      opt.textContent = "疯狂宅邸";
      sel.appendChild(opt);
    }
  }
}

// ---- 加载主题 ----
async function loadTheme() {
  try {
    const resp = await fetch("/api/theme");
    const theme = await resp.json();
    // 应用颜色
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
    // 应用字体
    if (theme.fonts) {
      if (theme.fonts.body) document.documentElement.style.setProperty("--font", theme.fonts.body);
      if (theme.fonts.mono) document.documentElement.style.setProperty("--font-mono", theme.fonts.mono);
    }
    // 应用标题
    if (theme.title) document.title = theme.title;
    const startTitle = document.getElementById("start-title");
    if (startTitle && theme.title) startTitle.textContent = `🏛 ${theme.title}`;
    const startSub = document.getElementById("start-subtitle");
    if (startSub && theme.subtitle) startSub.textContent = theme.subtitle;
    if (btnStart && theme.startButtonText) btnStart.textContent = theme.startButtonText;
  } catch { /* 主题加载失败不影响后续连接 */ }
}

// ---- 启动 ----
loadModules().then(() => loadTheme()).then(() => connect());
