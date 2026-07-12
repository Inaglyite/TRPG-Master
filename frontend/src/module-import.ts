/** 游戏开始界面的 .trpgmod 预检与导入流程。 */

import {
  btnImportModule,
  moduleFileInput,
  moduleImportStatus,
  moduleImportOverlay,
  moduleImportClose,
  moduleImportCancel,
  moduleImportConfirm,
  moduleImportName,
  moduleImportMeta,
  moduleImportDescription,
  moduleImportWarnings,
  moduleImportWarningList,
  moduleImportError,
} from "./dom";
import { safeSend } from "./ws";

const MAX_PACKAGE_BYTES = 64 * 1024 * 1024;
const BACKEND_HOST = location.hostname || "127.0.0.1";
const BACKEND_ORIGIN = `http://${BACKEND_HOST}:8765`;

type ModuleSummary = {
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

let pendingFile: File | null = null;
let pendingModule: ModuleSummary | null = null;
let inspectionSequence = 0;

function setStatus(text: string, state: "idle" | "working" | "success" | "error" = "idle") {
  moduleImportStatus.textContent = text;
  moduleImportStatus.dataset.state = state;
}

function resetPreview() {
  pendingModule = null;
  moduleImportName.textContent = "正在检查模组包…";
  moduleImportMeta.textContent = "";
  moduleImportDescription.textContent = "";
  moduleImportWarningList.replaceChildren();
  moduleImportWarnings.classList.add("hidden");
  moduleImportError.classList.add("hidden");
  moduleImportError.textContent = "";
  moduleImportConfirm.disabled = true;
  moduleImportConfirm.textContent = "导入并切换";
}

function openPreview() {
  resetPreview();
  moduleImportOverlay.classList.remove("hidden");
  moduleImportClose.focus();
}

function closePreview() {
  inspectionSequence++;
  moduleImportOverlay.classList.add("hidden");
  pendingFile = null;
  pendingModule = null;
  moduleFileInput.value = "";
  btnImportModule.disabled = false;
  btnImportModule.focus();
}

function showError(message: string, details: string[] = []) {
  const suffix = details.length > 0 ? `\n${details.join("\n")}` : "";
  moduleImportError.textContent = `${message}${suffix}`;
  moduleImportError.classList.remove("hidden");
  moduleImportConfirm.disabled = true;
}

async function uploadPackage(endpoint: "inspect" | "import", file: File) {
  const response = await fetch(`${BACKEND_ORIGIN}/api/modules/${endpoint}`, {
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
    const error = new Error(payload.error || `导入失败（HTTP ${response.status}）`);
    (error as any).details = Array.isArray(payload.details) ? payload.details : [];
    throw error;
  }
  return payload;
}

function renderSummary(summary: ModuleSummary) {
  pendingModule = summary;
  moduleImportName.textContent = summary.title;
  const author = summary.author || "未署名作者";
  moduleImportMeta.textContent = `${author} · v${summary.version} · ${summary.system || "未声明规则"} · ${summary.file_count} 个文件`;
  moduleImportDescription.textContent = summary.description || "这个模组没有提供简介。";
  moduleImportWarningList.replaceChildren();
  const warnings = summary.warnings || [];
  if (warnings.length > 0) {
    warnings.forEach((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      moduleImportWarningList.appendChild(item);
    });
    moduleImportWarnings.classList.remove("hidden");
  } else {
    moduleImportWarnings.classList.add("hidden");
  }
  moduleImportError.classList.add("hidden");
  moduleImportConfirm.disabled = false;
  moduleImportConfirm.focus();
}

async function inspectSelectedFile(file: File) {
  const sequence = ++inspectionSequence;
  pendingFile = file;
  openPreview();
  setStatus(`正在检查 ${file.name}`, "working");
  try {
    const payload = await uploadPackage("inspect", file);
    if (sequence !== inspectionSequence) return;
    renderSummary(payload.module as ModuleSummary);
    setStatus(`已通过格式与安全检查：${payload.module.title}`, "success");
  } catch (error) {
    if (sequence !== inspectionSequence) return;
    const message = error instanceof Error ? error.message : "无法检查模组包";
    const details = error instanceof Error && Array.isArray((error as any).details)
      ? (error as any).details
      : [];
    moduleImportName.textContent = "模组包无法导入";
    showError(message, details);
    setStatus(message, "error");
  }
}

async function confirmImport() {
  if (!pendingFile || !pendingModule) return;
  moduleImportConfirm.disabled = true;
  moduleImportCancel.disabled = true;
  moduleImportClose.disabled = true;
  moduleImportConfirm.textContent = "正在安装…";
  setStatus(`正在安装 ${pendingModule.title}`, "working");
  try {
    const payload = await uploadPackage("import", pendingFile);
    const imported = payload.module;
    moduleImportOverlay.classList.add("hidden");
    setStatus(
      payload.already_installed
        ? `「${imported.title}」已经安装，已为你切换`
        : `已导入「${imported.title}」v${imported.version}`,
      "success",
    );
    safeSend(JSON.stringify({ type: "switch_module", module: imported.id }));
    pendingFile = null;
    pendingModule = null;
    moduleFileInput.value = "";
  } catch (error) {
    const message = error instanceof Error ? error.message : "模组安装失败";
    const details = error instanceof Error && Array.isArray((error as any).details)
      ? (error as any).details
      : [];
    showError(message, details);
    setStatus(message, "error");
  } finally {
    moduleImportConfirm.disabled = pendingModule === null;
    moduleImportCancel.disabled = false;
    moduleImportClose.disabled = false;
    moduleImportConfirm.textContent = "导入并切换";
    btnImportModule.disabled = false;
  }
}

btnImportModule.onclick = () => {
  moduleFileInput.value = "";
  moduleFileInput.click();
};

moduleFileInput.onchange = () => {
  const file = moduleFileInput.files?.[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".trpgmod")) {
    setStatus("请选择扩展名为 .trpgmod 的模组包", "error");
    moduleFileInput.value = "";
    return;
  }
  if (file.size > MAX_PACKAGE_BYTES) {
    setStatus("模组包不能超过 64 MiB", "error");
    moduleFileInput.value = "";
    return;
  }
  btnImportModule.disabled = true;
  void inspectSelectedFile(file);
};

moduleImportConfirm.onclick = () => void confirmImport();
moduleImportClose.onclick = closePreview;
moduleImportCancel.onclick = closePreview;
moduleImportOverlay.addEventListener("click", (event) => {
  if (event.target === moduleImportOverlay && !moduleImportClose.disabled) closePreview();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !moduleImportOverlay.classList.contains("hidden") && !moduleImportClose.disabled) {
    closePreview();
  }
});
