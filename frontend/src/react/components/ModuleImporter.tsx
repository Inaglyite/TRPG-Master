import { useEffect, useRef, useState } from "react";

import {
  uploadModulePackage,
  type ModulePackageSummary,
} from "../../api/modulePackages";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { safeSend } from "../../ws";
import { useDelayedClose } from "./transitions";
import { ArchiveFolderPanel } from "./ArchiveFolderPanel";
import { useDialogKeyboard } from "./useDialogKeyboard";

const maxBytes = 64 * 1024 * 1024;
export function ModuleImporter() {
  const mode = useAppStore((state) => state.mode);
  return mode === "local" ? <LocalModuleImporter /> : null;
}

function LocalModuleImporter() {
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLElement>(null);
  const sequence = useRef(0);
  const alive = useRef(true);
  const upload = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<ModulePackageSummary | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [statusKind, setStatusKind] = useState("");
  const [error, setError] = useState("");
  // 延迟关闭：退出动画期间保留面板内容，隐藏后再清空表单状态。
  const { rendered, closing } = useDelayedClose(open);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      sequence.current++;
      upload.current?.abort();
    };
  }, []);
  useEffect(
    () =>
      useAppStore.subscribe((state, previous) => {
        if (
          previous.connection === "connected" &&
          state.connection !== "connected"
        ) {
          sequence.current++;
          upload.current?.abort();
          setBusy(false);
          setError(
            "连接已中断，尚未确认操作结果。请重新选择文件检查；已安装的模组不会被撤销。",
          );
          setSummary(null);
          setStatus("连接已中断，请重连后重新检查。");
          setStatusKind("error");
        }
      }),
    [],
  );
  useEffect(() => {
    if (rendered) return;
    setFile(null);
    setSummary(null);
    setError("");
    if (input.current) input.current.value = "";
  }, [rendered]);
  const close = () => {
    if (busy) return;
    sequence.current++;
    setOpen(false);
  };
  useDialogKeyboard(panel, open && rendered, busy, close, trigger);
  const beginUpload = () => {
    upload.current?.abort();
    upload.current = new AbortController();
    return upload.current.signal;
  };
  const isCurrent = (current: number) =>
    alive.current &&
    current === sequence.current &&
    useAppStore.getState().mode === "local" &&
    !useStartStore.getState().gameStarted;
  const inspect = async (selected: File) => {
    if (!selected.name.toLowerCase().endsWith(".trpgmod")) {
      setStatus("请选择扩展名为 .trpgmod 的模组包");
      setStatusKind("error");
      return;
    }
    if (selected.size > maxBytes) {
      setStatus("模组包不能超过 64 MiB");
      setStatusKind("error");
      return;
    }
    const current = ++sequence.current;
    const signal = beginUpload();
    setFile(selected);
    setSummary(null);
    setError("");
    setOpen(true);
    setBusy(true);
    setStatus(`正在检查 ${selected.name}`);
    setStatusKind("working");
    try {
      const payload = await uploadModulePackage("inspect", selected, signal);
      if (!isCurrent(current)) return;
      setSummary(payload.module);
      setStatus(`已通过格式与安全检查：${payload.module.title}`);
      setStatusKind("success");
    } catch (reason) {
      if (!isCurrent(current)) return;
      const caught = reason as Error & { details?: string[] };
      setError(
        [caught.message || "无法检查模组包", ...(caught.details || [])].join(
          "\n",
        ),
      );
      setStatus(caught.message);
      setStatusKind("error");
    } finally {
      if (isCurrent(current)) setBusy(false);
    }
  };
  const install = async () => {
    if (
      !file ||
      !summary ||
      busy ||
      useAppStore.getState().connection !== "connected"
    )
      return;
    const current = ++sequence.current;
    const signal = beginUpload();
    setError("");
    setBusy(true);
    setStatus(`正在安装 ${summary.title}`);
    setStatusKind("working");
    try {
      const payload = await uploadModulePackage("import", file, signal);
      if (!isCurrent(current)) return;
      const imported = payload.module;
      setStatus(
        payload.already_installed
          ? `「${imported.title}」已经安装，已请求切换`
          : `已导入「${imported.title}」v${imported.version}，已请求切换`,
      );
      setStatusKind("success");
      setOpen(false);
      safeSend(JSON.stringify({ type: "switch_module", module: imported.id }));
      setFile(null);
      setSummary(null);
    } catch (reason) {
      if (!isCurrent(current)) return;
      const caught = reason as Error & { details?: string[] };
      setError(
        [caught.message || "模组安装失败", ...(caught.details || [])].join(
          "\n",
        ),
      );
      setStatus(caught.message);
      setStatusKind("error");
    } finally {
      if (isCurrent(current)) setBusy(false);
    }
  };
  return (
    <>
      <button
        id="btn-import-module"
        ref={trigger}
        type="button"
        disabled={busy}
        onClick={() => {
          if (input.current) {
            input.current.value = "";
            input.current.click();
          }
        }}
      >
        ⇧ <span>导入模组</span>
      </button>
      <input
        ref={input}
        id="module-file-input"
        type="file"
        accept=".trpgmod,application/zip"
        hidden
        onChange={(event) => {
          const selected = event.target.files?.[0];
          if (selected) void inspect(selected);
        }}
      />
      <div
        id="module-import-status"
        data-state={statusKind || undefined}
        aria-live="polite"
      >
        {status}
      </div>
      {rendered && (
        <div
          id="module-import-overlay"
          className={closing ? "overlay-closing" : undefined}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) close();
          }}
        >
          <ArchiveFolderPanel
            ref={panel}
            className="module-folder"
            id="module-import-panel"
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby="module-import-title"
          >
            <div className="module-import-header">
              <div>
                <div className="module-import-eyebrow">
                  TRPGMOD / PACKAGE REVIEW
                </div>
                <h2 id="module-import-title">导入模组</h2>
              </div>
              <button
                id="module-import-close"
                type="button"
                aria-label="关闭模组导入"
                disabled={busy}
                onClick={close}
              >
                ✕
              </button>
            </div>
            <div className="module-import-body" data-dialog-scroll>
              <p className="module-import-source">
                本地模组库 · .trpgmod · 最大 64 MiB
              </p>
              <h3 id="module-import-name">
                {summary?.title ||
                  (error ? "模组包无法导入" : "正在检查模组包…")}
              </h3>
              {summary && (
                <>
                  <div id="module-import-meta">
                    {summary.author || "未署名作者"} · v{summary.version} ·{" "}
                    {summary.system || "未声明规则"} · {summary.file_count}{" "}
                    个文件
                  </div>
                  <p id="module-import-description">
                    {summary.description || "这个模组没有提供简介。"}
                  </p>
                  {Boolean(summary.warnings?.length) && (
                    <div id="module-import-warnings">
                      <div className="module-import-warning-title">
                        导入前请确认
                      </div>
                      <ul>
                        {summary.warnings.map((warning) => (
                          <li key={warning}>{warning}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
              {error && (
                <div id="module-import-error" aria-live="assertive">
                  {error}
                </div>
              )}
              {!summary && !busy && file && (
                <button
                  type="button"
                  className="btn-ghost module-retry"
                  onClick={() => void inspect(file)}
                >
                  重新检查
                </button>
              )}
            </div>
            <div className="module-import-actions">
              <button id="module-import-cancel" disabled={busy} onClick={close}>
                取消
              </button>
              <button
                id="module-import-confirm"
                disabled={busy || !summary}
                onClick={() => void install()}
              >
                {busy && summary ? "正在安装…" : "导入并切换"}
              </button>
            </div>
          </ArchiveFolderPanel>
        </div>
      )}
    </>
  );
}
