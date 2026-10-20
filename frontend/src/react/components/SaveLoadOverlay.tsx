import { useEffect, useRef, useState } from "react";
import { useStartStore } from "../../state/start-store";
import {
  finishSaveLoad,
  useSaveLoadStore,
  type SaveLoadPhase,
} from "../../state/save-load-store";
import { safeSend } from "../../ws";

const labels: Record<SaveLoadPhase, string> = {
  switching: "正在切换到存档所在的时间线…",
  restoring: "正在恢复存档进度…",
  history: "正在整理已保存的对话…",
  character: "正在同步调查员与场景…",
  presenting: "正在准备游戏画面…",
};

export function SaveLoadOverlay() {
  const progress = useSaveLoadStore((state) => state.progress);
  const gameStarted = useStartStore((state) => state.gameStarted);
  const [elapsed, setElapsed] = useState(0);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!progress) return;
    const previous = document.activeElement;
    panel.current?.focus();
    const update = () =>
      setElapsed(Math.floor((performance.now() - progress.startedAt) / 1000));
    update();
    const timer = window.setInterval(update, 1000);
    return () => {
      window.clearInterval(timer);
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, [progress?.requestId]);

  useEffect(() => {
    if (!progress || progress.phase !== "presenting" || !gameStarted) return;
    let frame = 0;
    // React has committed the history. Keep the cover through its first paint
    // and the start screen's exit, so the measured endpoint is the game screen.
    const waitForGame = () => {
      if (document.querySelector("#start-overlay:not(.hidden)")) {
        frame = requestAnimationFrame(waitForGame);
        return;
      }
      frame = requestAnimationFrame(() => {
        const metrics = finishSaveLoad(progress.requestId);
        if (metrics)
          safeSend(JSON.stringify({ type: "save_load_timing", ...metrics }));
        document.querySelector<HTMLTextAreaElement>("#user-input")?.focus();
      });
    };
    frame = requestAnimationFrame(waitForGame);
    return () => cancelAnimationFrame(frame);
  }, [progress?.requestId, progress?.phase, gameStarted]);

  if (!progress) return null;
  return (
    <div
      className="save-load-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="save-load-title"
      tabIndex={-1}
      ref={panel}
      onKeyDown={(event) => {
        if (event.key === "Tab") event.preventDefault();
      }}
    >
      <div className="save-load-card">
        <span className="save-load-spinner" aria-hidden="true" />
        <p className="save-load-eyebrow">重返故事</p>
        <h2 id="save-load-title">正在读取存档</h2>
        <p className="save-load-phase" role="status" aria-live="polite">
          {labels[progress.phase]}
        </p>
        <p className="save-load-elapsed">已等待 {elapsed} 秒</p>
        <p className="save-load-note">
          {elapsed >= 15
            ? "读取仍在进行，请稍候。完成后会自动进入游戏。"
            : "进度准备好后，将自动进入游戏。"}
        </p>
      </div>
    </div>
  );
}
