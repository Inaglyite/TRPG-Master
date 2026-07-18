import { useEffect, useRef, useState } from "react";

import { backendHttpOrigin } from "../../backend-url";
import { safeSend } from "../../ws";

const maxBytes = 64 * 1024 * 1024;
const backendOrigin = backendHttpOrigin();
type Summary = {
  module_key: string;
  package_id: string;
  version: string;
  title: string;
  author: string;
  description: string;
  system: string;
  capabilities: string[];
  file_count: number;
  warnings: string[];
};

async function upload(endpoint: "inspect" | "import", file: File) {
  const response = await fetch(`${backendOrigin}/api/modules/${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/vnd.trpg-master.module+zip",
      "X-Module-Filename": encodeURIComponent(file.name),
    },
    body: file,
  });
  let payload: any;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`守秘人返回了无法解析的响应（HTTP ${response.status}）`);
  }
  if (!response.ok || !payload.ok) {
    const error = new Error(
      payload.error || `导入失败（HTTP ${response.status}）`,
    );
    (error as Error & { details?: string[] }).details = Array.isArray(
      payload.details,
    )
      ? payload.details
      : [];
    throw error;
  }
  return payload;
}

export function ModuleImporter() {
  const input = useRef<HTMLInputElement>(null);
  const sequence = useRef(0);
  const [file, setFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [statusKind, setStatusKind] = useState("");
  const [error, setError] = useState("");
  const close = () => {
    if (busy) return;
    sequence.current++;
    setOpen(false);
    setFile(null);
    setSummary(null);
    setError("");
    if (input.current) input.current.value = "";
  };
  useEffect(() => {
    if (!open) return;
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, [open, busy]);
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
    setFile(selected);
    setSummary(null);
    setError("");
    setOpen(true);
    setBusy(true);
    setStatus(`正在检查 ${selected.name}`);
    setStatusKind("working");
    try {
      const payload = await upload("inspect", selected);
      if (current !== sequence.current) return;
      setSummary(payload.module);
      setStatus(`已通过格式与安全检查：${payload.module.title}`);
      setStatusKind("success");
    } catch (reason) {
      if (current !== sequence.current) return;
      const caught = reason as Error & { details?: string[] };
      setError(
        [caught.message || "无法检查模组包", ...(caught.details || [])].join(
          "\n",
        ),
      );
      setStatus(caught.message);
      setStatusKind("error");
    } finally {
      if (current === sequence.current) setBusy(false);
    }
  };
  const install = async () => {
    if (!file || !summary) return;
    setBusy(true);
    setStatus(`正在安装 ${summary.title}`);
    setStatusKind("working");
    try {
      const payload = await upload("import", file);
      const imported = payload.module;
      setStatus(
        payload.already_installed
          ? `「${imported.title}」已经安装，已为你切换`
          : `已导入「${imported.title}」v${imported.version}`,
      );
      setStatusKind("success");
      setOpen(false);
      safeSend(JSON.stringify({ type: "switch_module", module: imported.id }));
      setFile(null);
      setSummary(null);
    } catch (reason) {
      const caught = reason as Error & { details?: string[] };
      setError(
        [caught.message || "模组安装失败", ...(caught.details || [])].join(
          "\n",
        ),
      );
      setStatus(caught.message);
      setStatusKind("error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button
        id="btn-import-module"
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
      {open && (
        <div
          id="module-import-overlay"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) close();
          }}
        >
          <div
            id="module-import-panel"
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
              <button id="module-import-close" disabled={busy} onClick={close}>
                ✕
              </button>
            </div>
            <div className="module-import-body">
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
          </div>
        </div>
      )}
    </>
  );
}
