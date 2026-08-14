import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { abandonSoloWorld, enterSoloLobby } from "../../../online";
import { useOnlineStore } from "../../../state/online-store";

type ExitStep = "menu" | "confirm-abandon";

/**
 * 云端单人世界的上下文退出入口。
 *
 * 它只在单人房主的开局/游戏阶段出现：返回是可逆的本地导航；放弃则由
 * 专用服务端端点归档，绝不复用 settle_case，以免把中途离场记成结案。
 */
export function SoloAdventureExitControl() {
  const view = useOnlineStore((state) => state.view);
  const roomStatus = useOnlineStore((state) => state.roomStatus);
  const roomMetadata = useOnlineStore((state) => state.roomMetadata);
  const roomBusy = useOnlineStore((state) => state.roomBusy);
  const roomError = useOnlineStore((state) => state.roomError);
  const isOwner = useOnlineStore((state) => {
    const userId = state.user?.id;
    return (
      userId != null &&
      state.members.some(
        (member) => member.user_id === userId && member.role === "owner",
      )
    );
  });

  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<ExitStep>("menu");
  const [pausing, setPausing] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const visible =
    view === "room" &&
    isOwner &&
    roomMetadata?.play_mode === "solo" &&
    (roomStatus === "starting" || roomStatus === "playing");
  const busy = roomBusy || pausing;

  // 离开单人世界、归档成功或角色变更后，不让上个世界的对话框残留。
  useEffect(() => {
    if (!visible) {
      setOpen(false);
      setStep("menu");
      setPausing(false);
    }
  }, [visible]);

  useEffect(() => {
    if (!open) return;
    dialogRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        setOpen(false);
        setStep("menu");
        window.setTimeout(() => triggerRef.current?.focus(), 0);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, open]);

  if (!visible) return null;

  function close() {
    if (busy) return;
    setOpen(false);
    setStep("menu");
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }

  async function returnToAdventureList() {
    if (busy) return;
    setPausing(true);
    // enterSoloLobby 会立即断开当前房间并清理公共叙事；存档仍在服务端，
    // 玩家可以在“我的冒险”中继续。它不取消已经提交给守秘人的回合。
    await enterSoloLobby();
    setPausing(false);
  }

  async function confirmAbandon() {
    if (busy) return;
    const abandoned = await abandonSoloWorld();
    if (abandoned) {
      setOpen(false);
      setStep("menu");
    }
  }

  const dialog = open ? (
    <div
      className="solo-adventure-exit-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        ref={dialogRef}
        className="solo-adventure-exit-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="solo-adventure-exit-title"
        tabIndex={-1}
      >
        <div className="solo-adventure-exit-eyebrow">
          INVESTIGATOR / CURRENT ADVENTURE
        </div>
        {step === "menu" ? (
          <>
            <h2 id="solo-adventure-exit-title">离开当前冒险</h2>
            <p className="solo-adventure-exit-description">
              返回会保留当前进度。若守秘人已经开始本回合，叙事会在服务器继续完成；你稍后可从“我的冒险”继续调查。
            </p>
            <div className="solo-adventure-exit-actions">
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => void returnToAdventureList()}
              >
                {pausing ? "正在返回…" : "返回我的冒险（保留进度）"}
              </button>
              <button type="button" className="btn-ghost" onClick={close}>
                继续调查
              </button>
            </div>
            <button
              type="button"
              className="solo-adventure-exit-abandon-link"
              disabled={busy}
              onClick={() => setStep("confirm-abandon")}
            >
              放弃并删除存档
            </button>
          </>
        ) : (
          <>
            <h2 id="solo-adventure-exit-title">确认放弃冒险？</h2>
            <p className="solo-adventure-exit-description">
              当前云端存档将被归档且无法恢复。这不会被记作结案或通关。
            </p>
            <p className="solo-adventure-exit-warning">
              若守秘人仍在处理本回合，请等待叙事结束后再操作。
            </p>
            {roomError && (
              <p className="online-notice online-notice--error" role="alert">
                {roomError}
              </p>
            )}
            <div className="solo-adventure-exit-actions">
              <button
                type="button"
                className="online-danger"
                disabled={busy}
                onClick={() => void confirmAbandon()}
              >
                {roomBusy ? "正在放弃…" : "确认放弃并删除"}
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={busy}
                onClick={() => setStep("menu")}
              >
                返回
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  ) : null;

  return (
    <>
      <button
        ref={triggerRef}
        id="btn-solo-adventure-exit"
        type="button"
        title="离开当前冒险"
        aria-label="离开当前冒险"
        aria-expanded={open}
        disabled={busy}
        onClick={() => {
          if (busy) return;
          useOnlineStore.setState({ roomError: null });
          setStep("menu");
          setOpen(true);
        }}
      >
        离开冒险
      </button>
      {dialog && createPortal(dialog, document.body)}
    </>
  );
}
