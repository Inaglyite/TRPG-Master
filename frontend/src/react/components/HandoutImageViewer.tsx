import { useEffect, useRef, useState, type RefObject } from "react";
import { ArchiveFolderPanel } from "./ArchiveFolderPanel";
import { useDialogKeyboard } from "./useDialogKeyboard";
import "../../styles/components/handout-viewer.css";

type Props = {
  source: string;
  label: string;
  onClose: () => void;
  returnFocus: RefObject<HTMLButtonElement | null>;
};

/** Displays already-authorized material; never fetches a catalogue or changes state. */
export function HandoutImageViewer({
  source,
  label,
  onClose,
  returnFocus,
}: Props) {
  const panel = useRef<HTMLElement>(null);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [naturalSize, setNaturalSize] = useState(false);
  useEffect(() => {
    if (status !== "loading") return;
    const timer = window.setTimeout(() => setStatus("error"), 12000);
    return () => window.clearTimeout(timer);
  }, [status, attempt]);
  useDialogKeyboard(panel, true, false, onClose, returnFocus);
  const retry = () => {
    setStatus("loading");
    setAttempt((value) => value + 1);
  };
  return (
    <div
      className="handout-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <ArchiveFolderPanel
        ref={panel}
        variant="wide"
        className="handout-viewer"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
      >
        <header className="handout-viewer-header">
          <div>
            <p className="panel-eyebrow">INVESTIGATOR / PRESENTED MATERIAL</p>
            <h2>{label}</h2>
          </div>
          <button
            type="button"
            className="btn-ghost"
            aria-label="关闭材料查看"
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        <div
          className={`handout-viewer-body${naturalSize ? " handout-viewer-body--natural" : ""}`}
          data-dialog-scroll
        >
          {status === "loading" && <p role="status">正在加载图片…</p>}
          {status === "error" && (
            <div className="handout-viewer-error">
              <p role="alert">图片未能加载。你可以重试，或关闭后继续游戏。</p>
              <button type="button" className="btn-ghost" onClick={retry}>
                重新加载图片
              </button>
            </div>
          )}
          <img
            key={`${source}:${attempt}`}
            src={source}
            alt={label}
            hidden={status === "error"}
            onLoad={() => setStatus("ready")}
            onError={() => setStatus("error")}
          />
        </div>
        <footer className="handout-viewer-footer">
          <p>查看不会取得线索或执行行动。关闭后继续游戏。</p>
          <button
            type="button"
            className="btn-ghost"
            disabled={status !== "ready"}
            aria-pressed={naturalSize}
            onClick={() => setNaturalSize(!naturalSize)}
          >
            {naturalSize ? "适应窗口" : "原尺寸查看"}
          </button>
        </footer>
      </ArchiveFolderPanel>
    </div>
  );
}
