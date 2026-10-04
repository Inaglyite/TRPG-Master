import { useState } from "react";
import { createPortal } from "react-dom";

import { useStartStore } from "../../state/start-store";
import { CompactGameDialog } from "./CompactGameDialog";
import { PlayStylePicker } from "./online/PlayStylePicker";

/** Creation choice only. Existing worlds never change mode through this panel. */
export function LocalPlayStylePanel() {
  const [open, setOpen] = useState(false);
  const state = useStartStore();
  const label =
    state.executionProfile === "legacy"
      ? "经典 AI 叙事"
      : { human: "人类主持", assisted: "AI 辅助主持", agent: "AI 主持" }[
          state.keeperMode
        ];
  const busy = state.gameStarting || state.moduleSwitchPending;
  return (
    <>
      <button
        type="button"
        className="btn-ghost local-play-style-trigger"
        aria-label={`游玩方式：${label}`}
        aria-haspopup="dialog"
        disabled={busy}
        onClick={(event) => {
          event.currentTarget.focus();
          setOpen(true);
        }}
      >
        {label} · 更改
      </button>
      {open &&
        createPortal(
          <CompactGameDialog
            id="local-play-style-panel"
            title="游玩方式"
            closeLabel="关闭游玩方式"
            onClose={() => setOpen(false)}
            footer={
              <button
                type="button"
                className="btn-ghost panel-action-cancel"
                onClick={() => setOpen(false)}
              >
                关闭
              </button>
            }
          >
            <p className="panel-action-note">
              仅影响新建冒险，不改变已有冒险。
            </p>
            <PlayStylePicker
              structured={state.executionProfile === "structured_v1"}
              keeperMode={state.keeperMode}
              disabled={busy}
              solo
              onChange={({ structured, keeperMode }) =>
                useStartStore.setState({
                  executionProfile: structured ? "structured_v1" : "legacy",
                  keeperMode,
                })
              }
            />
          </CompactGameDialog>,
          document.body,
        )}
    </>
  );
}
