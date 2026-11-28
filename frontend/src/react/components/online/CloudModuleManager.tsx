import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { z } from "zod";

import { apiFetch } from "../../../api/client";
import type { ModuleInfo } from "../../../api/modules";
import { useOnlineStore } from "../../../state/online-store";
import { ArchiveFolderPanel } from "../ArchiveFolderPanel";
import { StartMenuIcon } from "../StartMenuIcon";
import { SortableModuleCards, useModuleOrdering } from "../SortableModuleCards";
import { orderModules } from "../../../module-order";
import { useDelayedClose } from "../transitions";
import { useDialogKeyboard } from "../useDialogKeyboard";

const removeResponseSchema = z.object({
  ok: z.literal(true),
  world_count: z.number().int().nonnegative().optional(),
  save_count: z.number().int().nonnegative().optional(),
  cleanup_pending: z.boolean().optional(),
});

function errorMessage(reason: unknown): string {
  return reason instanceof Error && reason.message
    ? reason.message
    : "操作失败，请稍后重试。";
}

export function CloudModuleManager({
  onRefresh,
}: {
  onRefresh: () => Promise<void>;
}) {
  const modules = useOnlineStore((state) => state.modules);
  const modulesStatus = useOnlineStore((state) => state.modulesStatus);
  const modulesError = useOnlineStore((state) => state.modulesError);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const cancelRemove = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  const requestGeneration = useRef(0);
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [pendingRemoval, setPendingRemoval] = useState<ModuleInfo | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const { rendered, closing } = useDelayedClose(open, 140);
  const busy = working || modulesStatus === "loading";
  const ordering = useModuleOrdering(
    modules,
    busy || Boolean(pendingRemoval),
    () => {
      setStatus("");
      setError("");
    },
  );
  const { cards, dirty, drag } = ordering;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      requestGeneration.current += 1;
    };
  }, []);

  useEffect(() => {
    if (pendingRemoval) cancelRemove.current?.focus();
  }, [pendingRemoval]);

  const close = () => {
    if (!busy) {
      drag.cancel();
      setOpen(false);
      setPendingRemoval(null);
    }
  };
  useDialogKeyboard(panel, open && rendered, busy, close, trigger);

  const isCurrent = (generation: number) =>
    alive.current && requestGeneration.current === generation;

  const refresh = async () => {
    const generation = ++requestGeneration.current;
    setWorking(true);
    setError("");
    try {
      await onRefresh();
    } catch (reason) {
      if (isCurrent(generation)) setError(errorMessage(reason));
    } finally {
      if (isCurrent(generation)) setWorking(false);
    }
  };

  const saveOrder = async () => {
    if (busy || !dirty || pendingRemoval) return;
    const ids = cards.map((item) => item.id);
    const { user, authOrigin } = useOnlineStore.getState();
    const generation = ++requestGeneration.current;
    setWorking(true);
    setError("");
    setStatus("");
    try {
      await apiFetch("/api/me/modules/order", removeResponseSchema, {
        method: "PUT",
        body: { module_ids: ids },
      });
      const current = useOnlineStore.getState();
      if (
        !isCurrent(generation) ||
        current.user?.id !== user?.id ||
        current.authOrigin !== authOrigin
      )
        return;
      useOnlineStore.setState({ modules: orderModules(current.modules, ids) });
      ordering.reset();
      setStatus("排序已保存");
      try {
        await onRefresh();
      } catch (reason) {
        if (isCurrent(generation)) {
          setError(`顺序已保存，但列表刷新失败：${errorMessage(reason)}`);
        }
      }
    } catch (reason) {
      if (isCurrent(generation)) {
        setError(`保存模组顺序失败：${errorMessage(reason)}`);
      }
    } finally {
      if (isCurrent(generation)) setWorking(false);
    }
  };

  const remove = async () => {
    const item = pendingRemoval;
    if (!item || busy) return;
    const generation = ++requestGeneration.current;
    setWorking(true);
    setError("");
    setStatus("");
    try {
      const removed = await apiFetch(
        `/api/me/modules/${encodeURIComponent(item.id)}`,
        removeResponseSchema,
        { method: "DELETE" },
      );
      if (!isCurrent(generation)) return;
      setPendingRemoval(null);
      setStatus(
        `已删除「${item.title}」及其 ${removed.world_count ?? 0} 条时间线、${removed.save_count ?? 0} 个存档点。重新导入不会恢复这些进度。${removed.cleanup_pending ? "部分临时文件尚待清理。" : ""}`,
      );
      try {
        await onRefresh();
      } catch (reason) {
        if (isCurrent(generation)) {
          setError(`模组已移除，但列表刷新失败：${errorMessage(reason)}`);
        }
      }
    } catch (reason) {
      if (isCurrent(generation)) {
        setError(
          `${errorMessage(reason)}；如连接中断，请刷新列表核对移除结果。`,
        );
      }
    } finally {
      if (isCurrent(generation)) setWorking(false);
    }
  };

  const catalogError =
    error ||
    modulesError ||
    (modulesStatus === "unsupported"
      ? "服务器暂不支持模组库。"
      : modulesStatus === "error"
        ? "模组列表加载失败，请刷新重试。"
        : "");

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="start-menu-button"
        title="模组管理"
        data-tooltip="模组管理"
        onClick={() => {
          setOpen(true);
          setStatus("");
          setError("");
          setPendingRemoval(null);
          ordering.reset();
          void refresh();
        }}
      >
        <StartMenuIcon kind="modules" />
        <span>模组管理</span>
      </button>
      {rendered &&
        createPortal(
          <div
            className={`module-manager-overlay${closing ? " overlay-closing" : ""}`}
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) close();
            }}
          >
            <ArchiveFolderPanel
              ref={panel}
              className="module-manager-panel module-library-panel"
              role="dialog"
              aria-modal="true"
              aria-labelledby="module-library-panel-title"
              tabIndex={-1}
            >
              <header className="module-manager-header">
                <h2 id="module-library-panel-title">模组管理</h2>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  aria-label="关闭模组管理"
                  onClick={close}
                >
                  关闭
                </button>
              </header>
              <div className="module-manager-body" data-dialog-scroll>
                <p className="module-manager-note" id="cloud-module-order-help">
                  拖动卡片左侧把手排序，保存后应用到首页。键盘可用 ↑ ↓ 调整。
                </p>
                <SortableModuleCards
                  ordering={ordering}
                  helpId="cloud-module-order-help"
                  blocked={busy || Boolean(pendingRemoval)}
                  action={(item) =>
                    item.source === "account"
                      ? {
                          label: "从库中移除",
                          name: `从模组库移除${item.title}`,
                          onClick: () => {
                            setPendingRemoval(item);
                            setError("");
                            setStatus("");
                          },
                        }
                      : null
                  }
                />
                {!busy && modules.length === 0 && !catalogError && (
                  <p>暂无可用模组。</p>
                )}
                {pendingRemoval && (
                  <section
                    className="module-manager-confirm"
                    aria-label="确认从模组库移除"
                  >
                    <h3>从模组库移除「{pendingRemoval.title}」？</h3>
                    <p>
                      将永久删除此版本及其 {pendingRemoval.world_count ?? 0}{" "}
                      条时间线、{pendingRemoval.save_count ?? 0}{" "}
                      个存档点（含自动存档）和回合记录，无法撤销。重新导入不会恢复这些进度，角色库保留。
                      仍在使用该版本的房间须先关闭。
                    </p>
                    <div className="module-manager-actions">
                      <button
                        ref={cancelRemove}
                        type="button"
                        className="btn-ghost"
                        disabled={busy}
                        onClick={() => setPendingRemoval(null)}
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void remove()}
                      >
                        确认从库中移除
                      </button>
                    </div>
                  </section>
                )}
              </div>
              <footer className="module-manager-actions module-library-footer">
                {catalogError && <p role="alert">{catalogError}</p>}
                <p role="status">
                  {busy
                    ? "正在处理…"
                    : status || (dirty ? "有未保存的排序" : "")}
                </p>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={
                    busy || Boolean(pendingRemoval) || Boolean(drag.preview)
                  }
                  onClick={() => {
                    void refresh();
                  }}
                >
                  刷新列表
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={
                    busy ||
                    !dirty ||
                    Boolean(pendingRemoval) ||
                    Boolean(drag.preview)
                  }
                  onClick={() => void saveOrder()}
                >
                  保存排序
                </button>
              </footer>
            </ArchiveFolderPanel>
          </div>,
          document.body,
        )}
    </>
  );
}
