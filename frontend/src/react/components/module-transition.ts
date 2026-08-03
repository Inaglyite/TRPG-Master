/**
 * 开始界面模组切换的“卷宗换页”过渡状态机
 * （docs/ARCHITECTURE.md §9.2）。
 *
 * - 用户选定新模组（moduleSwitchPending 升起）→ 立即 leaving：旧内容收暗缩放。
 * - 服务端确认（start-store activeModule 变化；theme 消息先于 module_list 到达，
 *   因此此时 --module-bg-image 已是新模组背景）→ 预加载新背景后 entering：
 *   背景双层交叉过渡 + 卷宗纸边掠过 + 内容归位。
 * - 首次挂载不播放；prefers-reduced-motion 直接落定；切换失败恢复原状；
 *   快速连续操作只保留最新目标，entering 定时器与预加载回调都会被取消。
 */

import { useEffect, useRef, useState } from "react";

import { useStartStore } from "../../state/start-store";

export type ModuleTransitionPhase = "idle" | "leaving" | "entering";

// 背景交叉 240ms 与内容进入 180ms 重叠播放，定时器略留缓冲统一收尾。
const ENTERING_MS = 260;

function currentModuleBgImage(): string | null {
  const value = document.documentElement.style
    .getPropertyValue("--module-bg-image")
    .trim();
  return value && value !== "none" ? value : null;
}

function imageSource(cssValue: string): string | null {
  const match = cssValue.match(/^url\((?:"([^"]*)"|'([^']*)'|([^)]*))\)$/);
  if (!match) return null;
  return match[1] ?? match[2] ?? match[3] ?? null;
}

function prefersReducedMotion(): boolean {
  return Boolean(
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
}

export function useModuleTransition(): {
  phase: ModuleTransitionPhase;
  outgoingBg: string | null;
  incomingBg: string | null;
} {
  const activeModule = useStartStore((state) => state.activeModule);
  const pending = useStartStore((state) => state.moduleSwitchPending);
  const [phase, setPhase] = useState<ModuleTransitionPhase>("idle");
  const [outgoingBg, setOutgoingBg] = useState<string | null>(null);
  const [incomingBg, setIncomingBg] = useState<string | null>(null);

  const phaseRef = useRef<ModuleTransitionPhase>("idle");
  const seenModuleRef = useRef<string | null>(null);
  const sourceModuleRef = useRef<string | null>(null);
  const enterTimerRef = useRef<number | null>(null);
  const preloaderRef = useRef<HTMLImageElement | null>(null);

  phaseRef.current = phase;

  const clearEnterTimer = () => {
    if (enterTimerRef.current !== null) {
      window.clearTimeout(enterTimerRef.current);
      enterTimerRef.current = null;
    }
  };

  const cancelPreloader = () => {
    const img = preloaderRef.current;
    if (img) {
      img.onload = null;
      img.onerror = null;
      preloaderRef.current = null;
    }
  };

  const settle = () => {
    clearEnterTimer();
    cancelPreloader();
    sourceModuleRef.current = null;
    setPhase("idle");
    setOutgoingBg(null);
    setIncomingBg(null);
  };

  const startEntering = (bg: string | null) => {
    clearEnterTimer();
    setIncomingBg(bg);
    setPhase("entering");
    enterTimerRef.current = window.setTimeout(() => {
      enterTimerRef.current = null;
      settle();
    }, ENTERING_MS);
  };

  // 新背景必须先完成预加载再交叉过渡；失败用新主题的静态回退背景，不闪白。
  const beginEntering = (confirmedBg: string | null) => {
    const src = confirmedBg ? imageSource(confirmedBg) : null;
    if (!src) {
      startEntering(null);
      return;
    }
    cancelPreloader();
    const img = new Image();
    preloaderRef.current = img;
    img.onload = () => {
      if (preloaderRef.current !== img) return;
      preloaderRef.current = null;
      startEntering(confirmedBg);
    };
    img.onerror = () => {
      if (preloaderRef.current !== img) return;
      preloaderRef.current = null;
      startEntering(null);
    };
    img.src = src;
  };

  // 用户发起切换：立即 leaving。首次挂载前没有已定模组、reduced-motion 下都不播放。
  useEffect(() => {
    if (!pending) return;
    if (!seenModuleRef.current) return;
    if (prefersReducedMotion()) return;
    // 快速连续操作：取消上一场 entering，只保留最新目标。
    settle();
    sourceModuleRef.current = activeModule;
    setOutgoingBg(currentModuleBgImage());
    setPhase("leaving");
  }, [pending]);

  // 模组落定：首次记录不播；leaving 中的切换开始入场；其余路径（世界恢复等）静默落定。
  useEffect(() => {
    if (!activeModule) return;
    if (!seenModuleRef.current) {
      seenModuleRef.current = activeModule;
      return;
    }
    if (seenModuleRef.current === activeModule) return;
    seenModuleRef.current = activeModule;
    if (phaseRef.current !== "leaving") return;
    if (prefersReducedMotion()) {
      settle();
      return;
    }
    beginEntering(currentModuleBgImage());
  }, [activeModule]);

  // pending 解除但模组没变：切换失败，恢复旧内容并取消动效，不留遮罩或禁用态。
  useEffect(() => {
    if (pending) return;
    if (phaseRef.current !== "leaving") return;
    if (sourceModuleRef.current !== activeModule) return; // 正常确认由上面的 effect 接管
    settle();
  }, [pending, activeModule]);

  // 卸载时清理，保证无 timer / 预加载回调残留。
  useEffect(
    () => () => {
      clearEnterTimer();
      cancelPreloader();
    },
    [],
  );

  return { phase, outgoingBg, incomingBg };
}
