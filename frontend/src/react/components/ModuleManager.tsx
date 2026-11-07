import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  deleteModule,
  listManagedModules,
  type ManagedModule,
} from "../../api/moduleManagement";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { StartMenuIcon } from "./StartMenuIcon";
import { ArchiveFolderPanel } from "./ArchiveFolderPanel";
import { useDialogKeyboard } from "./useDialogKeyboard";

export function ModuleManager() {
  const mode = useAppStore((state) => state.mode);
  return mode === "local" ? <LocalModuleManager /> : null;
}

function LocalModuleManager() {
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const cancelDelete = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [modules, setModules] = useState<ManagedModule[]>([]);
  const [selected, setSelected] = useState<ManagedModule | null>(null);
  useEffect(() => {
    if (selected) cancelDelete.current?.focus();
  }, [selected]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const close = () => {
    if (!busy) {
      setOpen(false);
      setSelected(null);
    }
  };
  useDialogKeyboard(panel, open, busy, close, trigger);
  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await listManagedModules();
      if (!alive.current) return;
      setModules(result.modules);
      useStartStore.setState({ modules: result.modules });
    } catch (reason) {
      if (alive.current) setError((reason as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const remove = async () => {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await deleteModule(selected.id);
      if (!alive.current) return;
      const remaining = modules.filter((item) => item.id !== selected.id);
      setModules(remaining);
      useStartStore.setState({ modules: remaining });
      setStatus(
        `已删除「${selected.title}」及其 ${result.world_count} 条时间线、${result.save_count} 个存档点。可以重新导入模组。${result.cleanup_pending ? "部分临时文件尚待清理。" : ""}`,
      );
      setSelected(null);
    } catch (reason) {
      if (alive.current)
        setError(
          `${(reason as Error).message}；如连接中断，请刷新列表核对删除结果。`,
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="start-menu-button"
        onClick={() => {
          setOpen(true);
          setStatus("");
          setSelected(null);
          void refresh();
        }}
      >
        <StartMenuIcon kind="modules" />
        <span>模组管理</span>
      </button>
      {open &&
        createPortal(
          <div
            className="module-manager-overlay"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) close();
            }}
          >
            <ArchiveFolderPanel
              ref={panel}
              className="module-manager-panel"
              role="dialog"
              aria-modal="true"
              aria-labelledby="module-manager-title"
              tabIndex={-1}
            >
              <header className="module-manager-header">
                <h2 id="module-manager-title">模组管理</h2>
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
                <p className="module-manager-note">
                  删除已导入模组时，会一并删除该版本的全部存档、时间线和回合记录。角色库保留。
                </p>
                {status && <p role="status">{status}</p>}
                {error && <p role="alert">{error}</p>}
                {busy && <p role="status">正在处理…</p>}
                {modules.map((item) => (
                  <article className="module-manager-entry" key={item.id}>
                    <div className="module-manager-info">
                      <h3>{item.title}</h3>
                      <p>
                        {item.source === "builtin"
                          ? "内置模组"
                          : `v${item.version}`}{" "}
                        · {item.world_count} 条时间线 · {item.save_count}{" "}
                        个存档点
                      </p>
                      {item.delete_block && (
                        <p className="module-manager-note">
                          {item.delete_block}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={busy || Boolean(item.delete_block)}
                      aria-label={`删除${item.title}`}
                      onClick={() => {
                        setSelected(item);
                        setError("");
                      }}
                    >
                      删除
                    </button>
                  </article>
                ))}
                {!busy && modules.length === 0 && !error && (
                  <p>暂无模组，请关闭面板后选择“导入模组”。</p>
                )}
                {selected && (
                  <section
                    className="module-manager-confirm"
                    aria-label="确认删除模组"
                  >
                    <h3>删除「{selected.title}」？</h3>
                    <p>
                      将永久删除该版本的模组文件、{selected.world_count}{" "}
                      条时间线及 {selected.save_count}{" "}
                      个存档点（含自动存档），无法撤销。重新导入不会恢复这些进度。
                    </p>
                    <div className="module-manager-actions">
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={busy}
                        ref={cancelDelete}
                        onClick={() => setSelected(null)}
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void remove()}
                      >
                        确认删除模组及存档
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
                    setSelected(null);
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
