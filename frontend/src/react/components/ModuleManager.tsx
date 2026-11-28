import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  deleteModule,
  listManagedModules,
  type ManagedModule,
} from "../../api/moduleManagement";
import {
  orderModules,
  readLocalModuleOrder,
  saveLocalModuleOrder,
} from "../../module-order";
import { switchModuleBeforeRemoval } from "../../start";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { StartMenuIcon } from "./StartMenuIcon";
import { SortableModuleCards, useModuleOrdering } from "./SortableModuleCards";
import { ArchiveFolderPanel } from "./ArchiveFolderPanel";
import { useDialogKeyboard } from "./useDialogKeyboard";
import { useDelayedClose } from "./transitions";

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
  const { rendered, closing } = useDelayedClose(open, 140);
  const active = useStartStore((state) => state.activeModule);
  const ordering = useModuleOrdering(modules, busy || Boolean(selected), () => {
    setError("");
    setStatus("");
  });
  const { cards, dirty, drag } = ordering;
  const fallback =
    selected &&
    (modules.find(
      (item) => item.source === "builtin" && item.id !== selected.id,
    ) ??
      modules.find((item) => item.id !== selected.id));
  const needsSwitch = selected?.id === active;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const close = () => {
    if (!busy) {
      drag.cancel();
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
      const ordered = orderModules(result.modules, readLocalModuleOrder());
      setModules(ordered);
      useStartStore.setState({ modules: ordered });
    } catch (reason) {
      if (alive.current) setError((reason as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const saveOrder = () => {
    if (busy || !dirty || selected || drag.preview) return;
    try {
      saveLocalModuleOrder(cards.map((item) => item.id));
      setModules(cards);
      useStartStore.setState({ modules: cards });
      ordering.reset();
      setError("");
      setStatus("排序已保存");
    } catch {
      setError("无法保存本地排序，请检查浏览器存储后重试。");
    }
  };
  const remove = async () => {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      if (useStartStore.getState().activeModule === selected.id) {
        if (!fallback) throw new Error("请先导入另一个模组以便切换");
        await switchModuleBeforeRemoval(fallback.id);
        if (!alive.current) return;
      }
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
        title="模组管理"
        data-tooltip="模组管理"
        onClick={() => {
          setOpen(true);
          ordering.reset();
          setStatus("");
          setSelected(null);
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
                <p className="module-manager-note" id="local-module-order-help">
                  拖动卡片左侧把手排序，保存后应用到首页。键盘可用 ↑ ↓ 调整。
                </p>
                <SortableModuleCards
                  ordering={ordering}
                  helpId="local-module-order-help"
                  blocked={busy || Boolean(selected)}
                  local
                  details={(item) => (
                    <>
                      {item.id === active && (
                        <span className="module-current-label">当前使用</span>
                      )}
                      <span className="module-save-count">
                        {" "}
                        · {item.world_count} 条时间线 · {item.save_count}{" "}
                        个存档点
                      </span>
                    </>
                  )}
                  action={(item) =>
                    item.source === "user"
                      ? {
                          label: "删除",
                          name: `删除${item.title}`,
                          onClick: () => {
                            setSelected(item);
                            setStatus("");
                            setError("");
                          },
                        }
                      : null
                  }
                />
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
                    {needsSwitch && (
                      <p>
                        {fallback
                          ? `删除前将切换到「${fallback.title}」。`
                          : "请先导入另一个模组以便切换。"}
                      </p>
                    )}
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
                        disabled={busy || (needsSwitch && !fallback)}
                        onClick={() => void remove()}
                      >
                        {needsSwitch
                          ? "切换并删除模组及存档"
                          : "确认删除模组及存档"}
                      </button>
                    </div>
                  </section>
                )}
              </div>
              <footer className="module-manager-actions module-library-footer">
                {error && <p role="alert">{error}</p>}
                <p role="status">
                  {busy
                    ? "正在处理…"
                    : status || (dirty ? "有未保存的排序" : "")}
                </p>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy || Boolean(selected) || Boolean(drag.preview)}
                  onClick={() => void refresh()}
                >
                  刷新列表
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={
                    busy || !dirty || Boolean(selected) || Boolean(drag.preview)
                  }
                  onClick={saveOrder}
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
