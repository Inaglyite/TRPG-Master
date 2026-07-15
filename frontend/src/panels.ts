/**
 * panels.ts — 面板 UI
 *
 * 负责：角色面板 (updateCharPanel)、线索面板 (updateCluePanel)、
 *       结局展示 (showEnding)、存档面板 (openSavePanel / closeSavePanel / renderSavePanel)。
 * 同时绑定存档 / 面板相关的按钮事件。
 */

import {
  optionsBar,
  charPanel,
  savePanelOverlay,
  savePanelTitle,
  savePanelClose,
  savePanelNew,
  savePanelList,
  worldPanelList,
  worldPanelSection,
  startOverlay,
  btnSave,
  btnLoad,
  btnPanel,
} from "./dom";
import { safeSend } from "./ws";
import { addMsg, removeLoading, scrollDown } from "./renderer";
import { getGameStarted } from "./start";
import { enableInput, sendAction } from "./options";
import { escapeHtml } from "./text";

type ClueAsset = {
  id?: string;
  file?: string;
  label?: string;
  asset_data_uri?: string;
  asset_url?: string;
};

type ClueItem = {
  id?: string;
  text?: string;
  type?: string;
  tier?: number;
  asset?: ClueAsset | null;
};

const CLUE_CATEGORIES = ["investigation", "event", "task", "npc"] as const;

const CLUE_NAMES: Record<string, string> = {
  investigation: "探案线索",
  event: "事件线索",
  task: "任务线索",
  npc: "人物线索",
};

let knownClueKeys: Set<string> | null = null;
let quickSavePending = false;
let quickSaveTimeout: number | undefined;
let quickSaveFeedbackTimeout: number | undefined;

const QUICK_SAVE_LABEL = "快速存档";

function parseJson<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function clearElement(el: Element) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

function clampPct(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function upsertStatBar(valueId: string, fillId: string, fillClass: string, pct: number) {
  const valueEl = document.getElementById(valueId);
  if (!valueEl) return;

  let fill = document.getElementById(fillId) as HTMLElement | null;
  if (!fill) {
    const bar = document.createElement("div");
    bar.className = "stat-bar";
    fill = document.createElement("div");
    fill.className = `stat-bar-fill ${fillClass}`;
    fill.id = fillId;
    bar.appendChild(fill);
    const row = valueEl.closest(".stat-row");
    (row || valueEl).insertAdjacentElement("afterend", bar);
  } else {
    const row = valueEl.closest(".stat-row");
    const bar = fill.parentElement;
    if (row && bar?.parentElement === row) row.insertAdjacentElement("afterend", bar);
  }
  fill.style.width = `${clampPct(pct)}%`;
}

function makeBadge(text: string, cls = "") {
  const badge = document.createElement("span");
  badge.className = `clue-tier${cls ? ` ${cls}` : ""}`;
  badge.textContent = text;
  return badge;
}

function openImageOverlay(src: string, alt = "") {
  const overlay = document.createElement("div");
  overlay.className = "handout-overlay";
  const img = document.createElement("img");
  img.src = src;
  img.alt = alt;
  overlay.appendChild(img);
  overlay.onclick = () => overlay.remove();
  document.body.appendChild(overlay);
}

function dismissHandout(card: HTMLElement) {
  if (!card.parentElement || card.classList.contains("leaving")) return;
  card.classList.add("leaving");
  setTimeout(() => card.remove(), 280);
}

// ---- 角色面板 ----
export function updateCharPanel(raw: string) {
  const data = parseJson<any>(raw, null);
  if (!data) return;
  try {
    // 名字和职业随模组动态更新
    const nameEl = document.getElementById("char-name");
    if (nameEl && data.name) nameEl.textContent = data.name;
    const occEl = document.getElementById("char-occupation");
    if (occEl && data.occupation) occEl.textContent = data.occupation;

    document.getElementById("hp-bar")!.textContent = `${data.hp} / ${data.max_hp}`;
    document.getElementById("san-bar")!.textContent = `${data.san} / ${data.max_san}`;
    const hpPct = (Number(data.hp) / Number(data.max_hp)) * 100;
    const sanPct = (Number(data.san) / Number(data.max_san)) * 100;
    upsertStatBar("hp-bar", "hp-fill", "hp", hpPct);
    upsertStatBar("san-bar", "san-fill", "san", sanPct);

    const attrs = data.attributes || {};
    const attrList = document.getElementById("attr-list")!;
    clearElement(attrList);
    Object.entries(attrs).forEach(([k, v]) => {
      const row = document.createElement("div");
      row.className = "stat-row";
      const label = document.createElement("span");
      label.textContent = k;
      const value = document.createElement("span");
      value.textContent = String(v);
      row.appendChild(label);
      row.appendChild(value);
      attrList.appendChild(row);
    });

    const inv = document.getElementById("inv-list")!;
    clearElement(inv);
    (data.inventory || []).forEach((s: string) => {
      const item = document.createElement("div");
      item.textContent = s;
      inv.appendChild(item);
    });
  } catch {
    // ignore parse errors
  }
}

// ---- 线索面板 ----
export function updateCluePanel(cluesRaw: string) {
  const clues = parseJson<Record<string, ClueItem[]>>(cluesRaw, {});
  const cluesList = document.getElementById("clues-list")!;
  clearElement(cluesList);
  notifyNewClues(clues);

  let rendered = 0;
  if (typeof clues === "object" && !Array.isArray(clues)) {
    for (const cat of CLUE_CATEGORIES) {
      const items = clues[cat] || [];
      if (items.length === 0) continue;

      const heading = document.createElement("div");
      heading.className = "clue-cat";
      heading.textContent = CLUE_NAMES[cat] || cat;
      cluesList.appendChild(heading);

      for (const item of items) {
        cluesList.appendChild(renderClueItem(item));
        rendered++;
      }
    }
  }

  if (rendered === 0) {
    const empty = document.createElement("div");
    empty.className = "clue-empty";
    empty.textContent = "暂无记录";
    cluesList.appendChild(empty);
  }
}

function collectClueKeys(clues: Record<string, ClueItem[]>): Set<string> {
  const keys = new Set<string>();
  if (typeof clues !== "object" || Array.isArray(clues)) return keys;
  for (const cat of CLUE_CATEGORIES) {
    const items = clues[cat] || [];
    items.forEach((item, index) => {
      if (item.type === "profile") return;
      const key = item.id || item.text || `${cat}-${index}`;
      keys.add(`${cat}:${key}`);
    });
  }
  return keys;
}

function notifyNewClues(clues: Record<string, ClueItem[]>) {
  const current = collectClueKeys(clues);
  if (knownClueKeys) {
    let added = 0;
    current.forEach((key) => {
      if (!knownClueKeys!.has(key)) added++;
    });
    if (added > 0) {
      showClueToast(added);
    }
  }
  knownClueKeys = current;
}

function showClueToast(count: number) {
  let stack = document.getElementById("toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "toast-stack";
    document.body.appendChild(stack);
  }

  const toast = document.createElement("div");
  toast.className = "clue-toast";
  toast.textContent = count > 1 ? `${count} 条线索已加入` : "线索已加入";
  stack.appendChild(toast);

  setTimeout(() => toast.classList.add("leaving"), 2200);
  setTimeout(() => toast.remove(), 2700);
}

function renderClueItem(item: ClueItem) {
  const row = document.createElement("div");
  row.className = "clue-item";
  if (item.type === "profile") row.classList.add("profile");

  const main = document.createElement("div");
  main.className = "clue-main";

  const text = document.createElement("div");
  text.className = "clue-text";
  text.textContent = item.text || "未命名线索";
  main.appendChild(text);

  const meta = document.createElement("div");
  meta.className = "clue-meta";
  if (item.type === "profile") meta.appendChild(makeBadge("人物", "profile"));
  if (item.tier === 2) meta.appendChild(makeBadge("TIER2"));
  if (item.type === "inferred") meta.appendChild(makeBadge("推理", "inferred"));
  if (item.asset?.file) meta.appendChild(makeBadge("图像", "asset"));
  if (meta.childElementCount > 0) main.appendChild(meta);

  row.appendChild(main);

  const assetSrc = item.asset?.asset_data_uri || item.asset?.asset_url || "";
  if (assetSrc) {
    row.classList.add("has-asset");
    const btn = document.createElement("button");
    btn.className = "clue-thumb-btn";
    btn.title = item.asset?.label || item.asset?.file || "查看线索图片";
    const img = document.createElement("img");
    img.className = "clue-thumb";
    img.src = assetSrc;
    img.alt = item.asset?.label || item.asset?.file || "线索图片";
    img.loading = "lazy";
    btn.appendChild(img);
    btn.onclick = () => openImageOverlay(assetSrc, img.alt);
    row.appendChild(btn);
  } else if (item.asset?.file) {
    row.classList.add("has-asset");
    const missing = document.createElement("div");
    missing.className = "clue-thumb-missing";
    missing.textContent = "图像不可用";
    missing.title = item.asset.file;
    row.appendChild(missing);
  }

  return row;
}

// ---- 请求角色状态 ----
export function loadState() {
  safeSend(JSON.stringify({ type: "state" }));
}

// ---- 展示材料（Handout） ----
export function showHandout(data: { file: string; label: string; asset_data_uri: string; asset_url: string; entity_type: string; entity_id: string }) {
  const container = document.getElementById("handout-container");
  if (!container) return;

  // 优先用 base64 data URI（electron file:// 下 HTTP URL 不可用），回退到 HTTP URL（web 模式）
  const imgSrc = data.asset_data_uri || data.asset_url;
  if (!imgSrc) return;  // 无图片则不弹窗

  const card = document.createElement("div");
  card.className = "handout-card";

  const header = document.createElement("div");
  header.className = "handout-header";
  const label = document.createElement("span");
  label.className = "handout-label";
  label.textContent = data.label || data.file;
  const close = document.createElement("button");
  close.className = "handout-close";
  close.textContent = "✕";
  close.onclick = () => dismissHandout(card);
  header.appendChild(label);
  header.appendChild(close);

  const img = document.createElement("img");
  img.src = imgSrc;
  img.alt = data.label || data.file;
  img.loading = "lazy";
  img.onclick = () => openImageOverlay(imgSrc, img.alt);

  card.appendChild(header);
  card.appendChild(img);
  container.appendChild(card);

  // 10 秒后自动消失
  setTimeout(() => dismissHandout(card), 10000);
}

export function clearTransientHandouts() {
  document.getElementById("handout-container")?.replaceChildren();
  document.querySelectorAll(".handout-overlay").forEach((overlay) => overlay.remove());
  document.getElementById("toast-stack")?.remove();
}

// ---- 结局展示 ----
export function showEnding(data: any) {
  removeLoading();
  const emoji = data.ending_type === "good" ? "🏆" : data.ending_type === "bad" ? "💀" : "🌫";
  // 显示结局提议按钮
  const btnConfirm = document.createElement("button");
  btnConfirm.className = "opt-btn end-confirm";
  btnConfirm.textContent = emoji + " 确认结束 —— " + data.title;
  btnConfirm.id = "btn-end-confirm";

  const btnContinueBtn = document.createElement("button");
  btnContinueBtn.className = "opt-btn free end-continue";
  btnContinueBtn.textContent = "🔄 继续探索";
  btnContinueBtn.id = "btn-end-continue";

  optionsBar.innerHTML = "";
  optionsBar.appendChild(btnConfirm);
  optionsBar.appendChild(btnContinueBtn);

  btnConfirm.onclick = () => {
    safeSend(JSON.stringify({
      type: "settle_case",
      ending_type: data.ending_type,
      title: data.title,
      summary: data.summary,
    }));
    const html = [
      '<div class="ending-box">',
      '<div class="ending-emoji">' + emoji + "</div>",
      '<div class="ending-title">' + escapeHtml(data.title) + "</div>",
      '<div class="ending-summary">' + escapeHtml(data.summary) + "</div>",
      "</div>",
    ].join("");
    const el = addMsg("gm", html);
    el.classList.add("ending");
    optionsBar.innerHTML = "";
    enableInput(false);
  };

  btnContinueBtn.onclick = () => {
    sendAction("继续探索");
  };
}

// ---- 存档面板 ----
export function openSavePanel(mode: "load" | "manage" = "manage") {
  // 如果开始界面还在显示，先隐藏
  if (!getGameStarted()) startOverlay.classList.add("hidden");
  savePanelOverlay.dataset.mode = mode;
  savePanelTitle.textContent = mode === "load" ? "从存档开始" : "存档管理";
  savePanelNew.classList.toggle("hidden", mode === "load");
  savePanelOverlay.classList.remove("hidden");
  safeSend(JSON.stringify({ type: "save_list" }));
  safeSend(JSON.stringify({ type: "world_list" }));
}

export function closeSavePanel() {
  savePanelOverlay.classList.add("hidden");
  // 如果游戏还没开始，恢复开始界面
  if (!getGameStarted()) startOverlay.classList.remove("hidden");
}

export function renderSavePanel(saves: any[]) {
  if (!saves || saves.length === 0) {
    savePanelList.innerHTML = '<div class="save-empty">暂无存档</div>';
    return;
  }
  let html = "";
  for (let i = 0; i < saves.length; i++) {
    const s = saves[i];
    const isAuto = s.id === "slot_000";
    const isLatest = i === 0;
    let timeStr = "未知时间";
    let relative = "";
    if (s.created_at) {
      try {
        const d = new Date(s.created_at);
        const diffMin = Math.floor((Date.now() - d.getTime()) / 60000);
        timeStr = d.toLocaleString("zh-CN", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        });
        if (diffMin < 1) relative = "刚刚";
        else if (diffMin < 60) relative = `${diffMin}分钟前`;
        else if (diffMin < 1440) relative = `${Math.floor(diffMin / 60)}小时前`;
        else relative = `${Math.floor(diffMin / 1440)}天前`;
      } catch {
        // ignore date parse errors
      }
    }
    const sceneName = escapeHtml(s.scene_name || "未知场景");
    const displayName = escapeHtml(s.label || s.scene_name || "未知场景");
    const characterName = escapeHtml(s.character_name || "未知调查员");
    const hpStr = escapeHtml(s.hp || "?");
    const sanStr = escapeHtml(s.san || "?");
    const slotId = escapeHtml(s.id);
    const clueCount = s.clue_count ?? 0;
    const msgCount = s.message_count ?? 0;

    html += `<div class="save-slot-entry${isLatest ? " save-latest" : ""}" data-slot="${slotId}">
      <div class="save-slot-info">
        <div class="save-slot-title">
          <span class="save-slot-name">${isLatest ? '<span class="save-badge">最新</span> ' : ""}${isAuto ? "💾" : "📁"} ${displayName}</span>
          <span class="save-slot-time">${escapeHtml(relative)} · ${escapeHtml(timeStr)}</span>
        </div>
        <div class="save-slot-meta">
          <span>${sceneName}</span>
          <span>${characterName}</span>
          <span>HP ${hpStr} SAN ${sanStr}</span>
          <span>📜 ${clueCount} 线索</span>
          <span>💬 ${msgCount} 消息</span>
        </div>
      </div>
      <div class="save-slot-actions">
        <button type="button" class="save-action-load" data-slot="${slotId}" data-tooltip="读取存档" aria-label="读取存档">📂</button>
        <button type="button" class="save-action-rename" data-slot="${slotId}" data-label="${escapeHtml(s.label || "")}" data-scene-name="${sceneName}" data-tooltip="重命名" aria-label="重命名存档">✏️</button>
        ${isAuto ? "" : `<button type="button" class="save-action-del" data-slot="${slotId}" data-tooltip="删除存档" aria-label="删除存档">🗑</button>`}
      </div>
    </div>`;
  }
  savePanelList.innerHTML = html;

  // Bind load buttons
  savePanelList.querySelectorAll(".save-action-load").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slot = (btn as HTMLElement).getAttribute("data-slot") || "";
      closeSavePanel();
      addMsg("system", "正在读档…");
      safeSend(JSON.stringify({ type: "save_load", slot_id: slot }));
    });
  });

  // Bind rename buttons
  savePanelList.querySelectorAll(".save-action-rename").forEach((btn) => {
    btn.addEventListener("click", () => {
      openSaveRenameEditor(btn as HTMLButtonElement);
    });
  });

  // Bind delete buttons
  savePanelList.querySelectorAll(".save-action-del").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slot = (btn as HTMLElement).getAttribute("data-slot") || "";
      safeSend(JSON.stringify({ type: "save_delete", slot_id: slot }));
    });
  });
}

export function renderWorldPanel(worlds: any[], activeWorldId: string) {
  if (!Array.isArray(worlds) || worlds.length === 0) {
    worldPanelSection.classList.add("hidden");
    worldPanelList.replaceChildren();
    return;
  }
  worldPanelSection.classList.remove("hidden");
  worldPanelList.replaceChildren();

  worlds.forEach((world) => {
    const worldId = String(world.world_id || "");
    if (!worldId) return;
    const active = Boolean(world.active) || worldId === activeWorldId;
    const entry = document.createElement("div");
    entry.className = `world-entry${active ? " active" : ""}`;

    const info = document.createElement("div");
    info.className = "world-entry-info";
    const title = document.createElement("div");
    title.className = "world-entry-title";
    title.textContent = String(world.label || (world.is_branch ? "时间线分支" : "主时间线"));
    const meta = document.createElement("div");
    meta.className = "world-entry-meta";
    meta.textContent = `${world.scene_name || "未知场景"} · ${world.character_name || "未知调查员"}`;
    info.append(title, meta);

    const action = document.createElement("button");
    action.type = "button";
    action.className = "world-switch-button";
    action.textContent = active ? "当前" : "↪";
    action.title = active ? "当前时间线" : "切换到此时间线";
    action.setAttribute("aria-label", active ? "当前时间线" : `切换到${title.textContent}`);
    action.disabled = active;
    action.onclick = () => {
      action.disabled = true;
      safeSend(JSON.stringify({ type: "world_switch", world_id: worldId }));
    };
    entry.append(info, action);
    worldPanelList.appendChild(entry);
  });
}

function openSaveRenameEditor(renameButton: HTMLButtonElement) {
  const entry = renameButton.closest(".save-slot-entry");
  const title = entry?.querySelector(".save-slot-title");
  const name = title?.querySelector(".save-slot-name") as HTMLElement | null;
  if (!entry || !title || !name || title.querySelector(".save-rename-form")) return;

  // Only keep one rename editor open so keyboard actions stay unambiguous.
  savePanelList.querySelectorAll(".save-rename-cancel").forEach((button) => {
    (button as HTMLButtonElement).click();
  });

  const slot = renameButton.dataset.slot || "";
  const sceneName = renameButton.dataset.sceneName || "未知场景";
  const form = document.createElement("div");
  form.className = "save-rename-form";

  const input = document.createElement("input");
  input.className = "save-rename-input";
  input.type = "text";
  input.maxLength = 50;
  input.value = renameButton.dataset.label || sceneName;
  input.placeholder = sceneName;
  input.setAttribute("aria-label", "存档名称");

  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "save-rename-confirm";
  confirm.textContent = "✓";
  confirm.dataset.tooltip = "保存名称";
  confirm.setAttribute("aria-label", "保存存档名称");

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "save-rename-cancel";
  cancel.textContent = "×";
  cancel.dataset.tooltip = "取消";
  cancel.setAttribute("aria-label", "取消重命名");

  const restoreName = () => {
    form.remove();
    name.hidden = false;
    renameButton.focus();
  };
  const submitName = () => {
    if (form.classList.contains("saving")) return;
    form.classList.add("saving");
    input.disabled = true;
    confirm.disabled = true;
    cancel.disabled = true;
    safeSend(JSON.stringify({ type: "save_rename", slot_id: slot, label: input.value.trim() }));
  };

  confirm.addEventListener("click", submitName);
  cancel.addEventListener("click", restoreName);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitName();
    } else if (event.key === "Escape") {
      event.preventDefault();
      restoreName();
    }
  });

  form.append(input, confirm, cancel);
  name.hidden = true;
  title.prepend(form);
  input.focus();
  input.select();
}

function quickSave() {
  if (quickSavePending || !getGameStarted()) return;

  window.clearTimeout(quickSaveFeedbackTimeout);
  quickSavePending = true;
  btnSave.disabled = true;
  btnSave.classList.remove("save-success", "save-failed");
  btnSave.classList.add("saving");
  btnSave.title = "保存中…";
  btnSave.setAttribute("aria-label", "正在快速存档");
  safeSend(JSON.stringify({ type: "save", manual: false }));

  quickSaveTimeout = window.setTimeout(() => {
    finishQuickSave(false);
  }, 8000);
}

export function finishQuickSave(ok: boolean) {
  if (!quickSavePending) return;

  quickSavePending = false;
  window.clearTimeout(quickSaveTimeout);
  btnSave.disabled = false;
  btnSave.classList.remove("saving");
  btnSave.classList.toggle("save-success", ok);
  btnSave.classList.toggle("save-failed", !ok);
  btnSave.title = ok ? "已保存" : "保存失败";
  btnSave.setAttribute("aria-label", ok ? "快速存档已完成" : "快速存档失败");

  quickSaveFeedbackTimeout = window.setTimeout(() => {
    btnSave.classList.remove("save-success", "save-failed");
    btnSave.title = QUICK_SAVE_LABEL;
    btnSave.setAttribute("aria-label", QUICK_SAVE_LABEL);
  }, 1600);
}

// ==================== 按钮事件绑定 ====================

btnSave.onclick = quickSave;
btnLoad.onclick = () => openSavePanel("manage");
btnPanel.onclick = () => {
  charPanel.classList.toggle("collapsed");
  if (!charPanel.classList.contains("collapsed")) loadState();
};

savePanelClose.onclick = closeSavePanel;
savePanelNew.onclick = () => {
  safeSend(JSON.stringify({ type: "save_create" }));
  addMsg("system", "正在保存…");
};

// Click outside panel to close
savePanelOverlay.onclick = (e) => {
  if (e.target === savePanelOverlay) closeSavePanel();
};
