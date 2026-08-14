/**
 * 通用过渡 hooks（ui-animation skill 规范：只动 transform/opacity、
 * 进出成对、reduced-motion 直接落定、快速连续操作只保留最新目标）。
 */

import { useEffect, useRef, useState } from "react";

export function prefersReducedMotion(): boolean {
  return Boolean(
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
}

/**
 * 浮层延迟关闭：open 变 false 后保持挂载 exitMs 毫秒并标记 closing
 * （供 CSS 播放退出动画），期间重新打开会立即取消退出。
 * reduced-motion 下立即卸载（连定时器延迟都没有）。
 */
export function useDelayedClose(
  open: boolean,
  exitMs = 160,
): { rendered: boolean; closing: boolean } {
  const [rendered, setRendered] = useState(open);
  const [closing, setClosing] = useState(false);
  const timerRef = useRef<number | null>(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => {
    if (open) {
      // 退出途中重新打开：取消退出，内容直接回到展示态。
      clearTimer();
      setRendered(true);
      setClosing(false);
      return;
    }
    if (!rendered) return;
    if (prefersReducedMotion()) {
      setRendered(false);
      setClosing(false);
      return;
    }
    setClosing(true);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setRendered(false);
      setClosing(false);
    }, exitMs);
  }, [open, rendered, exitMs]);

  // 卸载时清理，保证无定时器残留。
  useEffect(() => clearTimer, []);

  return { rendered, closing };
}

export type PhaseTransitionPhase = "idle" | "leaving" | "entering";

/**
 * 单挂载两阶段换场：目标值变化时先以旧内容播 leaving（exitMs），
 * 再换成新内容播 entering（enterMs）。leaving 途中目标回到当前值
 * （用户反悔）会取消换场且不补播进入动画。
 */
export function usePhaseTransition<T>(
  value: T,
  keyOf: (item: T) => string,
  { exitMs = 120, enterMs = 200 }: { exitMs?: number; enterMs?: number } = {},
): { displayed: T; phase: PhaseTransitionPhase } {
  const [displayed, setDisplayed] = useState(value);
  const [phase, setPhase] = useState<PhaseTransitionPhase>("idle");
  const timersRef = useRef<number[]>([]);

  const clearTimers = () => {
    for (const timer of timersRef.current) window.clearTimeout(timer);
    timersRef.current = [];
  };

  useEffect(() => {
    if (keyOf(value) === keyOf(displayed)) {
      // leaving 途中目标回到当前展示值：取消换场，不补播进入动画。
      if (phase === "leaving") {
        clearTimers();
        setPhase("idle");
      }
      return;
    }
    if (prefersReducedMotion()) {
      clearTimers();
      setDisplayed(value);
      setPhase("idle");
      return;
    }
    clearTimers();
    setPhase("leaving");
    timersRef.current.push(
      window.setTimeout(() => {
        setDisplayed(value);
        setPhase("entering");
        timersRef.current.push(
          window.setTimeout(() => setPhase("idle"), enterMs),
        );
      }, exitMs),
    );
  }, [value, displayed, phase, keyOf, exitMs, enterMs]);

  // 卸载时清理，保证无定时器残留。
  useEffect(() => clearTimers, []);

  return { displayed, phase };
}
