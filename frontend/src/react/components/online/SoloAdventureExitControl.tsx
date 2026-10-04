import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { abandonSoloWorld, enterSoloLobby } from "../../../online";
import { useOnlineStore } from "../../../state/online-store";
import { AdventureArchiveConfirmation } from "./AdventureArchiveConfirmation";
import { focusableControls, trapDialogTab } from "../dialogFocus";
import { ArchiveFolderPanel } from "../ArchiveFolderPanel";

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
  const worldId = useOnlineStore((state) => state.activeWorldId);
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
  const [archiving, setArchiving] = useState(false);
  const archiveInFlight = useRef(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);

  const visible =
    view === "room" &&
    isOwner &&
    roomMetadata?.play_mode === "solo" &&
    (roomStatus === "starting" || roomStatus === "playing");
  const busy = roomBusy || pausing || archiving;

  // 离开单人世界、归档成功或角色变更后，不让上个世界的对话框残留。
  useEffect(() => {
    if (!visible) {
      setOpen(false);
      setStep("menu");
      setPausing(false);
    }
  }, [visible]);

  useEffect(() => {
    setOpen(false);
    setStep("menu");
  }, [worldId]);

  useEffect(() => {
    if (!open) return;
    dialogRef.current?.querySelector<HTMLElement>(".solo-exit-keep")?.focus();
    return () => {
      const trigger = triggerRef.current;
      if (trigger?.isConnected && !trigger.disabled) trigger.focus();
    };
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    const active = document.activeElement;
    if (
      !dialog.contains(active) ||
      (active instanceof HTMLElement && active.matches(":disabled")) ||
      active === dialog
    ) {
      const safe = dialog.querySelector<HTMLButtonElement>(
        ".solo-exit-keep, .adventure-archive-actions .btn-ghost",
      );
      const controls = focusableControls(dialog);
      (safe && !safe.disabled ? safe : (controls[0] ?? dialog)).focus();
    }
  }, [open, busy, step]);

  if (!visible) return null;

  function close() {
    if (busy) return;
    setOpen(false);
    setStep("menu");
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
    if (busy || archiveInFlight.current) return;
    archiveInFlight.current = true;
    setArchiving(true);
    try {
      const abandoned = await abandonSoloWorld();
      if (abandoned) {
        setOpen(false);
        setStep("menu");
      }
    } finally {
      archiveInFlight.current = false;
      setArchiving(false);
    }
  }

  const dialog = open ? (
    <div
      className="solo-adventure-exit-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <ArchiveFolderPanel
        variant="wide"
        ref={dialogRef}
        className="solo-adventure-exit-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={
          step === "menu" ? "solo-adventure-exit-title" : undefined
        }
        aria-label={step === "confirm-abandon" ? "归档这场冒险？" : undefined}
        tabIndex={-1}
        aria-busy={busy}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            if (busy) return;
            if (step === "confirm-abandon") setStep("menu");
            else close();
          } else if (dialogRef.current) trapDialogTab(event, dialogRef.current);
        }}
      >
        {step === "menu" ? (
          <>
            <div className="solo-exit-heading">
              <div className="solo-adventure-exit-eyebrow">
                当前冒险 / INVESTIGATOR
              </div>
              <h2 id="solo-adventure-exit-title">离开当前冒险</h2>
            </div>
            <div className="solo-exit-body">
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
              </div>
              <button
                type="button"
                className="solo-adventure-exit-abandon-link"
                disabled={busy}
                onClick={() => setStep("confirm-abandon")}
              >
                放弃并归档冒险
              </button>
            </div>
            <footer className="solo-exit-footer">
              <button
                type="button"
                className="btn-ghost solo-exit-keep"
                disabled={busy}
                onClick={close}
              >
                继续调查
              </button>
            </footer>
          </>
        ) : (
          <AdventureArchiveConfirmation
            title={String(roomMetadata?.name || "当前冒险")}
            busy={busy}
            error={roomError}
            onConfirm={() => void confirmAbandon()}
            onCancel={() => setStep("menu")}
          />
        )}
      </ArchiveFolderPanel>
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
        <span className="exit-chevron" aria-hidden="true">
          ‹
        </span>
        离开冒险
      </button>
      {dialog && createPortal(dialog, document.body)}
    </>
  );
}
