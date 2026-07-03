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
  savePanelClose,
  savePanelNew,
  savePanelList,
  startOverlay,
  btnSave,
  btnLoad,
  btnPanel,
} from "./dom";
import { safeSend } from "./ws";
import { addMsg, removeLoading, scrollDown } from "./renderer";
import { getGameStarted } from "./start";
import { enableInput, sendAction } from "./options";

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
    const hpPct = (data.hp / data.max_hp) * 100;
    const sanPct = (data.san / data.max_san) * 100;

    // HP bar
    let hpBar = document.getElementById("hp-fill");
    if (!hpBar) {
      const bar = document.createElement("div");
      bar.className = "stat-bar";
      const fill = document.createElement("div");
      fill.className = "stat-bar-fill hp";
      fill.id = "hp-fill";
      fill.style.width = `${hpPct}%`;
      bar.appendChild(fill);
      document.getElementById("hp-bar")!.after(bar);
    } else {
      hpBar.style.width = `${hpPct}%`;
    }

    let sanBar = document.getElementById("san-fill");
    if (!sanBar) {
      const bar = document.createElement("div");
      bar.className = "stat-bar";
      const fill = document.createElement("div");
      fill.className = "stat-bar-fill san";
      fill.id = "san-fill";
      fill.style.width = `${sanPct}%`;
      bar.appendChild(fill);
      document.getElementById("san-bar")!.after(bar);
    } else {
      sanBar.style.width = `${sanPct}%`;
    }

    const attrs = data.attributes || {};
    const attrList = document.getElementById("attr-list")!;
    attrList.innerHTML = Object.entries(attrs)
      .map(([k, v]) => `<div class="stat-row"><span>${k}</span><span>${v}</span></div>`)
      .join("");

    const inv = document.getElementById("inv-list")!;
    inv.innerHTML = (data.inventory || []).map((s: string) => `<div>• ${s}</div>`).join("");
  } catch {
    // ignore parse errors
  }
}

// ---- 线索面板 ----
export function updateCluePanel(cluesRaw: string) {
  const clues = parseJson<Record<string, ClueItem[]>>(cluesRaw, {});
  const cluesList = document.getElementById("clues-list")!;
  clearElement(cluesList);

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

function renderClueItem(item: ClueItem) {
  const row = document.createElement("div");
  row.className = "clue-item";

  const main = document.createElement("div");
  main.className = "clue-main";

  const text = document.createElement("div");
  text.className = "clue-text";
  text.textContent = item.text || "未命名线索";
  main.appendChild(text);

  const meta = document.createElement("div");
  meta.className = "clue-meta";
  if (item.tier === 2) meta.appendChild(makeBadge("TIER2"));
  if (item.type === "inferred") meta.appendChild(makeBadge("推理", "inferred"));
  if (item.asset?.file) meta.appendChild(makeBadge("图像", "asset"));
  if (meta.childElementCount > 0) main.appendChild(meta);

  row.appendChild(main);

  const assetSrc = item.asset?.asset_data_uri || item.asset?.asset_url || "";
  if (assetSrc) {
    const btn = document.createElement("button");
    btn.className = "clue-thumb-btn";
    btn.title = item.asset?.label || item.asset?.file || "查看线索图片";
    const img = document.createElement("img");
    img.className = "clue-thumb";
    img.src = assetSrc;
    img.alt = item.asset?.label || item.asset?.file || "线索图片";
    btn.appendChild(img);
    btn.onclick = () => openImageOverlay(assetSrc, img.alt);
    row.appendChild(btn);
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
  close.onclick = () => card.remove();
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
  setTimeout(() => { if (card.parentElement) card.remove(); }, 10000);
}

// ---- 结局展示 ----
export function showEnding(data: any) {
  removeLoading();
  const emoji = data.ending_type === "good" ? "🏆" : data.ending_type === "bad" ? "💀" : "🌫";
  // 显示结局提议按钮
  const btnConfirm = document.createElement("button");
  btnConfirm.className = "opt-btn";
  btnConfirm.style.cssText = "flex:2;background:var(--gold);color:var(--bg);font-weight:bold";
  btnConfirm.textContent = emoji + " 确认结束 —— " + data.title;
  btnConfirm.id = "btn-end-confirm";

  const btnContinueBtn = document.createElement("button");
  btnContinueBtn.className = "opt-btn";
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
      '<div class="ending-title">' + data.title + "</div>",
      '<div class="ending-summary">' + data.summary + "</div>",
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
export function openSavePanel() {
  // 如果开始界面还在显示，先隐藏
  if (!getGameStarted()) startOverlay.classList.add("hidden");
  savePanelOverlay.classList.remove("hidden");
  safeSend(JSON.stringify({ type: "save_list" }));
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
    const sceneName = s.scene_name || "未知场景";
    const displayName = s.label || sceneName;
    const characterName = s.character_name || "未知调查员";
    const hpStr = s.hp || "?";
    const sanStr = s.san || "?";
    const clueCount = s.clue_count ?? 0;
    const msgCount = s.message_count ?? 0;

    html += `<div class="save-slot-entry${isLatest ? " save-latest" : ""}" data-slot="${s.id}">
      <div class="save-slot-info">
        <div class="save-slot-title">
          <span class="save-slot-name">${isLatest ? '<span class="save-badge">最新</span> ' : ""}${isAuto ? "💾" : "📁"} ${displayName}</span>
          <span class="save-slot-time">${relative} · ${timeStr}</span>
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
        <button class="save-action-load" data-slot="${s.id}">📂</button>
        <button class="save-action-rename" data-slot="${s.id}">✏️</button>
        ${isAuto ? "" : `<button class="save-action-del" data-slot="${s.id}">🗑</button>`}
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
      const slot = (btn as HTMLElement).getAttribute("data-slot") || "";
      const label = prompt("存档名称（留空用场景名）：");
      if (label !== null) {
        safeSend(JSON.stringify({ type: "save_rename", slot_id: slot, label: label.trim() }));
      }
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

// ==================== 按钮事件绑定 ====================

btnSave.onclick = openSavePanel;
btnLoad.onclick = openSavePanel;
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
