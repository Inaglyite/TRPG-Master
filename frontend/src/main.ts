/**
 * main.ts — 入口
 *
 * 职责仅限于导入各模块（触发模块内按钮绑定）→ loadTheme() → connect()。
 * 所有功能逻辑已拆分到以下模块：
 *   dom.ts       DOM 元素引用     panels.ts    角色/存档/结局面板
 *   ws.ts        WebSocket 通信   options.ts   选项/输入/检定弹窗
 *   renderer.ts  消息渲染/流式    start.ts     开局流程/游戏状态
 */

import { connect } from "./ws";
import { btnStart } from "./dom";

// ---- 加载主题 ----
async function loadTheme() {
  try {
    const resp = await fetch("/api/theme");
    const theme = await resp.json();
    // 应用颜色
    if (theme.colors) {
      const map: Record<string, string> = {
        bg: "--bg",
        bg2: "--bg2",
        bg3: "--bg3",
        bg4: "--bg4",
        text: "--text",
        textDim: "--text-dim",
        textFaint: "--text-faint",
        gold: "--gold",
        goldDim: "--gold-dim",
        goldBright: "--gold-bright",
        red: "--red",
        redBright: "--red-bright",
        green: "--green",
        blue: "--blue",
        blueBright: "--blue-bright",
        purple: "--purple",
        border: "--border",
        borderBright: "--border-bright",
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
  } catch {
    // 主题加载失败不影响后续连接
  }
}

// ---- 启动 ----
loadTheme().then(() => connect());
