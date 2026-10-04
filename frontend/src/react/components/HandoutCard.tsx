import { useCallback, useEffect, useRef, useState } from "react";
import { useAppStore, type Handout } from "../../state/app-store";
import { HandoutImageViewer } from "./HandoutImageViewer";
import { prefersReducedMotion } from "./transitions";

/** Arrival toast only; full-size reading pauses its automatic dismissal. */
export function HandoutCard({ handout }: { handout: Handout }) {
  const dismiss = useAppStore((state) => state.dismissHandout);
  const [expanded, setExpanded] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeTimer = useRef<number | null>(null);
  const label = handout.label || handout.file || "展示材料";
  const source = handout.asset_data_uri || handout.asset_url;
  const close = useCallback(() => {
    if (closeTimer.current !== null) return;
    if (prefersReducedMotion()) {
      dismiss(handout.id);
      return;
    }
    setLeaving(true);
    closeTimer.current = window.setTimeout(() => dismiss(handout.id), 280);
  }, [dismiss, handout.id]);
  useEffect(() => {
    if (expanded || hovered || focused || leaving) return;
    const timer = window.setTimeout(close, 10000);
    return () => window.clearTimeout(timer);
  }, [expanded, hovered, focused, leaving, close]);
  useEffect(
    () => () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    },
    [],
  );
  return (
    <>
      <div
        className={`handout-card${leaving ? " leaving" : ""}`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocusCapture={() => setFocused(true)}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setFocused(false);
        }}
      >
        <div className="handout-header">
          <span className="handout-label">{label}</span>
          <button
            type="button"
            className="handout-close"
            aria-label={`收起材料提示：${label}`}
            onClick={close}
          >
            ✕
          </button>
        </div>
        <button
          ref={trigger}
          type="button"
          className="handout-open"
          aria-label={`查看材料：${label}`}
          disabled={leaving}
          onClick={() => setExpanded(true)}
        >
          {!imageFailed && (
            <img
              src={source}
              alt={label}
              loading="lazy"
              onError={() => setImageFailed(true)}
            />
          )}
          <span>{imageFailed ? "预览未能加载，打开查看" : "打开查看"}</span>
        </button>
      </div>
      {expanded && (
        <HandoutImageViewer
          key={source}
          source={source}
          label={label}
          onClose={() => setExpanded(false)}
          returnFocus={trigger}
        />
      )}
    </>
  );
}
