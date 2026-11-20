import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { z } from "zod";

import { apiFetch } from "../../../api/client";
import type { ModuleInfo } from "../../../api/modules";
import { useOnlineStore } from "../../../state/online-store";
import { ArchiveFolderPanel } from "../ArchiveFolderPanel";
import { StartMenuIcon } from "../StartMenuIcon";
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

  const move = async (item: ModuleInfo, index: number, offset: -1 | 1) => {
    const target = index + offset;
    if (busy || target < 0 || target >= modules.length) return;
    const ordered = [...modules];
    ordered.splice(index, 1);
    ordered.splice(target, 0, item);
    const generation = ++requestGeneration.current;
    setWorking(true);
    setError("");
    setStatus("");
    setPendingRemoval(null);
    try {
      await apiFetch("/api/me/modules/order", removeResponseSchema, {
        method: "PUT",
        body: { module_ids: ordered.map((module) => module.id) },
      });
      if (!isCurrent(generation)) return;
      setStatus(
        `已将「${item.title}」${offset < 0 ? "上移" : "下移"}，当前第 ${target + 1} 位。`,
      );
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
              className="module-manager-panel cloud-module-manager"
              role="dialog"
              aria-modal="true"
              aria-labelledby="cloud-module-manager-title"
              tabIndex={-1}
            >
              <header className="module-manager-header">
                <h2 id="cloud-module-manager-title">模组管理</h2>
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
                {status && <p role="status">{status}</p>}
                {catalogError && <p role="alert">{catalogError}</p>}
                {busy && <p role="status">正在处理…</p>}
                <p className="module-manager-note">
                  调整主页模组列表的显示顺序，或从库中移除个人上传的模组。选择模组请使用主页下拉框。
                </p>
                {modules.map((item, index) => (
                  <article className="module-manager-entry" key={item.id}>
                    <div className="module-manager-info">
                      <h3>{item.title}</h3>
                      <p>
                        {item.source === "account" ? "个人上传" : "服务器模组"}{" "}
                        · v{item.version}
                      </p>
                      {item.description && <p>{item.description}</p>}
                    </div>
                    <div className="module-manager-actions">
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={busy || index === 0}
                        aria-label={`将${item.title}上移`}
                        onClick={() => void move(item, index, -1)}
                      >
                        上移
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={busy || index === modules.length - 1}
                        aria-label={`将${item.title}下移`}
                        onClick={() => void move(item, index, 1)}
                      >
                        下移
                      </button>
                      {item.source === "account" && (
                        <button
                          type="button"
                          className="btn-ghost"
                          disabled={busy}
                          aria-label={`从模组库移除${item.title}`}
                          onClick={() => {
                            setPendingRemoval(item);
                            setError("");
                            setStatus("");
                          }}
                        >
                          从库中移除
                        </button>
                      )}
                    </div>
                  </article>
                ))}
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
              <footer className="module-manager-actions">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={() => {
                    setPendingRemoval(null);
                    void refresh();
                  }}
                >
                  刷新列表
                </button>
              </footer>
            </ArchiveFolderPanel>
          </div>,
          document.body,
        )}
    </>
  );
}
