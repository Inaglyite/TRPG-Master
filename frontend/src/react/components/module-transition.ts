/**
 * 开始界面模组切换的“翻页”过渡状态机（docs/ARCHITECTURE.md §9.2）。
 *
 * - 用户选定新模组（moduleSwitchPending 升起）→ 立即 flipping：
 *   旧页面（旧背景图层 + 冻结的旧内容）绕左侧装订线 3D 翻起并加速翻走，
 *   新背景图层垫在下方，随翻页从右向左被逐步揭开。
 * - 服务端确认（activeModule 变化；theme 消息先于 module_list 到达，
 *   因此此时 --module-bg-image 已是新模组背景）→ 预加载新背景后垫入下层。
 * - 翻页动画结束且新背景就绪 → entering：旧页已翻走，此刻换装内容
 *   用户不可见，新内容淡入归位。网络慢于翻页时页面先翻走停在背景态，
 *   确认到达再 entering；切换失败恢复原状；快速连续操作只保留最新目标；
 *   首次挂载不播放；prefers-reduced-motion 直接落定。
 */

import { useEffect, useRef, useState } from "react";

import { useStartStore } from "../../state/start-store";

export type ModuleTransitionPhase = "idle" | "flipping" | "entering";

// 翻页 520ms（先加速的 ease-in，模拟书页被掀起后加速翻走），内容进入 220ms。
export const MODULE_FLIP_MS = 520;
const ENTERING_MS = 220;

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
  /** undefined=新背景未就绪（下层不渲染）；null=就绪但无图（回退默认背景） */
  incomingBg: string | null | undefined;
} {
  const activeModule = useStartStore((state) => state.activeModule);
  const pending = useStartStore((state) => state.moduleSwitchPending);
  const [phase, setPhase] = useState<ModuleTransitionPhase>("idle");
  const [outgoingBg, setOutgoingBg] = useState<string | null>(null);
  const [incomingBg, setIncomingBg] = useState<string | null | undefined>(
    undefined,
  );

  const phaseRef = useRef<ModuleTransitionPhase>("idle");
  const seenModuleRef = useRef<string | null>(null);
  const sourceModuleRef = useRef<string | null>(null);
  const flipDoneRef = useRef(false);
  const incomingReadyRef = useRef(false);
  const flipTimerRef = useRef<number | null>(null);
  const enterTimerRef = useRef<number | null>(null);
  const preloaderRef = useRef<HTMLImageElement | null>(null);

  phaseRef.current = phase;

  const clearTimers = () => {
    for (const ref of [flipTimerRef, enterTimerRef]) {
      if (ref.current !== null) {
        window.clearTimeout(ref.current);
        ref.current = null;
      }
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
    clearTimers();
    cancelPreloader();
    sourceModuleRef.current = null;
    flipDoneRef.current = false;
    incomingReadyRef.current = false;
    setPhase("idle");
    setOutgoingBg(null);
    setIncomingBg(undefined);
  };

  const startEntering = () => {
    clearTimers();
    setPhase("entering");
    enterTimerRef.current = window.setTimeout(() => {
      enterTimerRef.current = null;
      settle();
    }, ENTERING_MS);
  };

  const onFlipEnd = () => {
    flipTimerRef.current = null;
    flipDoneRef.current = true;
    // 新背景已就绪（网络快于翻页）：翻页落定立刻进入；否则停在背景态等确认。
    if (incomingReadyRef.current) startEntering();
  };

  // 新背景必须先完成预加载再垫入下层；失败用新主题的静态回退背景，不闪白。
  const beginEntering = (confirmedBg: string | null) => {
    const finishPreload = (bg: string | null) => {
      incomingReadyRef.current = true;
      setIncomingBg(bg);
      // 翻页已结束（网络慢于翻页）：直接补上 entering。
      if (phaseRef.current === "flipping" && flipDoneRef.current)
        startEntering();
    };
    const src = confirmedBg ? imageSource(confirmedBg) : null;
    if (!src) {
      finishPreload(null);
      return;
    }
    cancelPreloader();
    const img = new Image();
    preloaderRef.current = img;
    img.onload = () => {
      if (preloaderRef.current !== img) return;
      preloaderRef.current = null;
      finishPreload(confirmedBg);
    };
    img.onerror = () => {
      if (preloaderRef.current !== img) return;
      preloaderRef.current = null;
      finishPreload(null);
    };
    img.src = src;
  };

  // 用户发起切换：立即翻页。首次挂载前没有已定模组、reduced-motion 下都不播放。
  useEffect(() => {
    if (!pending) return;
    if (!seenModuleRef.current) return;
    if (prefersReducedMotion()) return;
    // 快速连续操作：取消上一场过渡，只保留最新目标。
    settle();
    sourceModuleRef.current = activeModule;
    setOutgoingBg(currentModuleBgImage());
    setPhase("flipping");
    flipTimerRef.current = window.setTimeout(onFlipEnd, MODULE_FLIP_MS);
  }, [pending]);

  // 模组落定：首次记录不播；翻页中的切换开始预加载新背景；其余路径静默落定。
  useEffect(() => {
    if (!activeModule) return;
    if (!seenModuleRef.current) {
      seenModuleRef.current = activeModule;
      return;
    }
    if (seenModuleRef.current === activeModule) return;
    seenModuleRef.current = activeModule;
    if (phaseRef.current !== "flipping") return;
    if (prefersReducedMotion()) {
      settle();
      return;
    }
    beginEntering(currentModuleBgImage());
  }, [activeModule]);

  // pending 解除但模组没变：切换失败，恢复旧内容并取消动效，不留遮罩或禁用态。
  useEffect(() => {
    if (pending) return;
    if (phaseRef.current !== "flipping") return;
    if (sourceModuleRef.current !== activeModule) return; // 正常确认由上面的 effect 接管
    settle();
  }, [pending, activeModule]);

  // 卸载时清理，保证无 timer / 预加载回调残留。
  useEffect(
    () => () => {
      clearTimers();
      cancelPreloader();
    },
    [],
  );

  return { phase, outgoingBg, incomingBg };
}

export function useCloudModuleTransition(
  selectedId: string,
  backgrounds: Record<string, string | null>,
): {
  displayedId: string;
  phase: ModuleTransitionPhase;
  outgoingBg: string | null;
  incomingBg: string | null | undefined;
} {
  const [displayedId, setDisplayedId] = useState(selectedId);
  const [phaseState, setPhaseState] = useState<ModuleTransitionPhase>("idle");
  const [incoming, setIncoming] = useState<{
    id: string;
    background: string | null;
  } | null>(null);
  const backgroundsRef = useRef(backgrounds);
  backgroundsRef.current = backgrounds;

  const reduced = prefersReducedMotion();
  const switching = displayedId !== selectedId;
  const animate = switching && Boolean(displayedId) && !reduced;
  const phase: ModuleTransitionPhase = animate
    ? "flipping"
    : switching || phaseState === "flipping"
      ? "idle"
      : phaseState;
  const visibleId = animate ? displayedId : selectedId;

  useEffect(() => {
    if (!displayedId || displayedId === selectedId || prefersReducedMotion()) {
      setDisplayedId(selectedId);
      setPhaseState("idle");
      setIncoming(null);
      return;
    }

    let cancelled = false;
    let flipDone = false;
    let imageReady = false;
    let flipTimer: number | null = null;
    let enterTimer: number | null = null;
    let preloader: HTMLImageElement | null = null;
    const targetBackground = backgroundsRef.current[selectedId] ?? null;
    setPhaseState("flipping");
    setIncoming(null);

    const clearTimers = () => {
      if (flipTimer !== null) window.clearTimeout(flipTimer);
      if (enterTimer !== null) window.clearTimeout(enterTimer);
      flipTimer = null;
      enterTimer = null;
    };
    const startEntering = () => {
      if (cancelled) return;
      clearTimers();
      setDisplayedId(selectedId);
      setPhaseState("entering");
      enterTimer = window.setTimeout(() => {
        if (cancelled) return;
        setPhaseState("idle");
        setIncoming(null);
      }, ENTERING_MS);
    };
    const maybeEnter = () => {
      if (flipDone && imageReady) startEntering();
    };
    flipTimer = window.setTimeout(() => {
      flipTimer = null;
      flipDone = true;
      maybeEnter();
    }, MODULE_FLIP_MS);

    const src = targetBackground ? imageSource(targetBackground) : null;
    if (!src) {
      imageReady = true;
      setIncoming({ id: selectedId, background: null });
      maybeEnter();
    } else {
      const img = new Image();
      preloader = img;
      img.onload = () => {
        if (cancelled || preloader !== img) return;
        preloader = null;
        imageReady = true;
        setIncoming({ id: selectedId, background: targetBackground });
        maybeEnter();
      };
      img.onerror = () => {
        if (cancelled || preloader !== img) return;
        preloader = null;
        imageReady = true;
        setIncoming({ id: selectedId, background: null });
        maybeEnter();
      };
      img.src = src;
    }

    return () => {
      cancelled = true;
      clearTimers();
      if (preloader) {
        preloader.onload = null;
        preloader.onerror = null;
      }
    };
  }, [selectedId]);

  return {
    displayedId: visibleId,
    phase,
    outgoingBg: backgroundsRef.current[displayedId] ?? null,
    incomingBg:
      incoming?.id === selectedId && phase !== "idle"
        ? incoming.background
        : undefined,
  };
}
