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
 * （以及 local 模式的模组背景图）一次性拉入 HTTP 缓存后再放行。
 * 后续进入时缓存命中，整个流程通常在数百毫秒内完成，仅作防白屏 splash。
 */
export function BootLoader() {
  const mode = useAppStore((state) => state.mode);
  const [visible, setVisible] = useState(true);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const firstBootRef = useRef<boolean>(isBuildChanged());
  const manifestDoneRef = useRef(false);
  const localDoneRef = useRef(false);

  // dist 资源预载：应用启动即跑，与模式无关（模式选择页本身也用这些图）。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const files = await loadBootManifest();
      if (cancelled) return;
      setProgress({ loaded: 0, total: files.length });
      await preloadImages(files, (loaded, total) => {
        if (!cancelled) setProgress({ loaded, total });
      });
      if (cancelled) return;
      manifestDoneRef.current = true;
      recordBuildBooted();
      if (useAppStore.getState().mode !== "local" || localDoneRef.current) {
        setVisible(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // local 模式：等 WS 首连（首批存档/角色数据随连接推送）与模组背景图。
  useEffect(() => {
    if (mode !== "local") return;
    let cancelled = false;
    void (async () => {
      const [bgUrl] = await Promise.all([
        waitForModuleBgUrl(),
        waitForConnection(),
      ]);
      if (bgUrl) await preloadImages([bgUrl]);
      if (cancelled) return;
      localDoneRef.current = true;
      if (manifestDoneRef.current) setVisible(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [mode]);

  if (!visible) return null;
  const showProgress = firstBootRef.current && progress.total > 0;
  const percent = showProgress
    ? Math.round((progress.loaded / progress.total) * 100)
    : 0;
  return (
    <div className="boot-loader" role="status" aria-live="polite">
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
    </div>
  );
}
