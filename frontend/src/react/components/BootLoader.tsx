import { useEffect, useRef, useState } from "react";

import {
  isBuildChanged,
  loadBootManifest,
  preloadImages,
  recordBuildBooted,
  waitForConnection,
  waitForModuleBgUrl,
} from "../../boot/preload";
import { useAppStore } from "../../state/app-store";

/**
 * 启动加载屏：版本更新后的首次进入显示进度条，把 dist 内全部 UI 图片
 * （以及 local 模式的模组背景图）拉进 HTTP 缓存；后续进入缓存命中，
 * 通常几百毫秒结束，仅作防白屏 splash。
 *
 * 放行条件（这两条是**有界**的，不再等整批图片）：应用已可交互——
 * 清单已拿到，且 local 模式的首连与模组背景图已就绪（各自都有短上限）；
 * 图片预载仍在后台继续，进度条只作展示。任何情况下最多挡
 * `SPLASH_BUDGET_MS`，避免「一张图片既不 onload 也不 onerror」把用户
 * 锁在加载屏上（CI 上就是这么挡住开局点击的）。
 * 资源没拿到时给出可见但不阻断的说明，而不是静默卡住。
 */

/** 加载屏最多遮挡交互的时长：到点即放行，剩余资源后台继续缓存。 */
export const SPLASH_BUDGET_MS = 6_000;

/** 应用已可交互、但资源仍在预载时的宽限期：正常首载在此内结束，不打扰用户。 */
const PRELOAD_GRACE_MS = 1_200;

export function BootLoader() {
  const mode = useAppStore((state) => state.mode);
  const [phase, setPhase] = useState<"loading" | "leaving" | "gone">("loading");
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [notice, setNotice] = useState("");
  const firstBootRef = useRef<boolean>(isBuildChanged());
  const manifestReadyRef = useRef(false);
  const localDoneRef = useRef(false);
  const preloadDoneRef = useRef(false);
  const leaveBegunRef = useRef(false);
  const graceTimerRef = useRef<number | null>(null);

  // 完成态稍停一拍（进度条 100% 被看见），再进入退场；退场动画由 CSS 承载，
  // 900ms 后与样式时长对齐卸载组件。
  const beginLeave = () => {
    if (leaveBegunRef.current) return;
    leaveBegunRef.current = true;
    if (graceTimerRef.current !== null) {
      window.clearTimeout(graceTimerRef.current);
      graceTimerRef.current = null;
    }
    window.setTimeout(() => setPhase("leaving"), 250);
    window.setTimeout(() => setPhase("gone"), 250 + 900);
  };

  /**
   * 应用已可交互时的放行判定。资源仍在后台预载不构成阻塞：给一小段宽限
   * （正常首载通常在宽限内结束，不显示任何提示），宽限过后照样放行并给说明。
   */
  const maybeLeave = () => {
    if (leaveBegunRef.current) return;
    if (!manifestReadyRef.current) return;
    if (useAppStore.getState().mode === "local" && !localDoneRef.current) {
      return;
    }
    if (preloadDoneRef.current) {
      beginLeave();
      return;
    }
    if (graceTimerRef.current !== null) return;
    graceTimerRef.current = window.setTimeout(() => {
      graceTimerRef.current = null;
      if (preloadDoneRef.current) {
        beginLeave();
        return;
      }
      setNotice("界面资源仍在后台加载，不影响开始时操作。");
      beginLeave();
    }, PRELOAD_GRACE_MS);
  };

  // dist 资源预载：应用启动即跑，与模式无关（模式选择页本身也用这些图）。
  // 它**不**决定何时放行交互（放行由 maybeLeave 的两条有界条件 + 预算决定）。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const files = await loadBootManifest();
      if (cancelled) return;
      // 放行只看**有界**的那部分（清单已拿到）；图片预载随后在后台继续。
      manifestReadyRef.current = true;
      setProgress({ loaded: 0, total: files.length });
      maybeLeave();
      const { failed } = await preloadImages(files, (loaded, total, bad) => {
        if (!cancelled) setProgress({ loaded, total });
        if (bad > 0) {
          setNotice(`部分界面资源未能加载（${bad} 项），不影响操作。`);
        }
      });
      if (cancelled) return;
      preloadDoneRef.current = true;
      if (failed > 0) {
        setNotice(`部分界面资源未能加载（${failed} 项），不影响操作。`);
      }
      recordBuildBooted();
      maybeLeave();
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // local 模式：等 WS 首连（首批存档/角色数据随连接推送）与模组背景图。
  // 背景图本身不阻塞放行：它跟着后台预载走。
  useEffect(() => {
    if (mode !== "local") return;
    let cancelled = false;
    void (async () => {
      const [bgUrl] = await Promise.all([
        waitForModuleBgUrl(),
        waitForConnection(),
      ]);
      if (cancelled) return;
      localDoneRef.current = true;
      if (bgUrl) void preloadImages([bgUrl]);
      maybeLeave();
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  // 兜底预算：不管资源什么状态，加载屏都不会一直挡着交互。
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (!preloadDoneRef.current && !leaveBegunRef.current) {
        setNotice("界面资源仍在后台加载，不影响开始时操作。");
      }
      beginLeave();
    }, SPLASH_BUDGET_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase === "gone") return null;
  const showProgress = firstBootRef.current && progress.total > 0;
  const percent = showProgress
    ? Math.round((progress.loaded / progress.total) * 100)
    : 0;
  return (
    <div
      className={`boot-loader${phase === "leaving" ? " boot-loader--leaving" : ""}`}
      role="status"
      aria-live="polite"
    >
      <div className="boot-loader-title">TRPG Game</div>
      <div className="boot-loader-bar">
        <div
          className="boot-loader-bar-fill"
          style={{ width: `${showProgress ? percent : 100}%` }}
        />
      </div>
      {showProgress && (
        <div className="boot-loader-progress">
          正在加载资源 {progress.loaded}/{progress.total}
        </div>
      )}
      {notice && <div className="boot-loader-notice">{notice}</div>}
    </div>
  );
}
