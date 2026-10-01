import { useEffect, useMemo, useRef, useState } from "react";

import {
  createLibraryEntry,
  deleteLibraryEntry,
  duplicateLibraryEntry,
  exportLibraryEntry,
  getLibraryCard,
  inspectLibraryCard,
  listCharacterLibrary,
  updateLibraryEntry,
  type LibraryEntry,
  type LibraryIssue,
} from "../../api/characterLibrary";
import { ApiError } from "../../api/client";
import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import { useStartStore, type CharacterOption } from "../../state/start-store";
import { safeSend } from "../../ws";
import {
  CHARACTER_ATTRIBUTE_LABELS,
  CHARACTER_SKILL_LABELS,
  CharacterDossier,
} from "./CharacterDossier";
import { useDelayedClose } from "./transitions";

/**
 * 角色库管理面板：列表 / 详情预览 / 新建与编辑 / 导入角色卡。
 * 本地与云端单人共用（同一组 API；归属由服务端按登录态隔离）。
 * 开局选角页的角色列表走 WS character_list（同一数据源）：库变更后
 * 由 notifyLibraryChanged 触发重推，无需刷新整页。
 */

const MAX_FILE_BYTES = 256 * 1024;
const ATTRIBUTE_IDS = Object.keys(CHARACTER_ATTRIBUTE_LABELS);

type EditorDraft = {
  name: string;
  occupation: string;
  era: string;
  age: string;
  attributes: Record<string, string>;
  luck: string;
  creditRating: string;
  skills: { id: string; value: string }[];
  inventoryText: string;
  description: string;
  background: string;
  keyConnection: string;
};

function emptyDraft(): EditorDraft {
  return {
    name: "",
    occupation: "",
    era: "1920年代",
    age: "",
    attributes: Object.fromEntries(ATTRIBUTE_IDS.map((id) => [id, "50"])),
    luck: "50",
    creditRating: "0",
    skills: [],
    inventoryText: "",
    description: "",
    background: "",
    keyConnection: "",
  };
}

function inventoryLabel(item: unknown): string {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return "";
  const value = item as Record<string, unknown>;
  return String(value.label ?? value.name ?? value.id ?? "");
}

function draftFromEntry(entry: LibraryEntry): EditorDraft {
  const backstory = entry.backstory || {};
  const text = (value: unknown) => (typeof value === "string" ? value : "");
  return {
    name: entry.name,
    occupation: entry.occupation,
    era: entry.era || "",
    age: entry.age != null ? String(entry.age) : "",
    attributes: Object.fromEntries(
      ATTRIBUTE_IDS.map((id) => [id, String(entry.attributes?.[id] ?? 50)]),
    ),
    luck: String(entry.derived?.LUCK ?? 50),
    creditRating: String(entry.credit_rating ?? 0),
    skills: Object.entries(entry.skills || {}).map(([id, value]) => ({
      id,
      value: String(value),
    })),
    inventoryText: (entry.inventory || [])
      .map(inventoryLabel)
      .filter(Boolean)
      .join("\n"),
    description: text(backstory.description),
    background: text(backstory.background),
    keyConnection: text(backstory.key_connection),
  };
}

/** 编辑器草稿 → 卡面 payload。数值解析失败时抛出带字段名的中文错误。 */
function draftToCard(
  draft: EditorDraft,
  original: Record<string, unknown> = {},
): Record<string, unknown> {
  const attributes: Record<string, number> = {};
  for (const id of ATTRIBUTE_IDS) {
    const value = Number(draft.attributes[id]);
    if (!Number.isInteger(value)) {
      throw new Error(
        `属性 ${CHARACTER_ATTRIBUTE_LABELS[id] || id} 必须是整数`,
      );
    }
    attributes[id] = value;
  }
  const luck = Number(draft.luck);
  if (!Number.isInteger(luck)) throw new Error("幸运（LUCK）必须是整数");
  const credit = Number(draft.creditRating);
  if (!Number.isInteger(credit)) throw new Error("信用评级必须是整数");
  const skills: Record<string, number> = {};
  for (const row of draft.skills) {
    const id = row.id.trim();
    if (!id) continue;
    const value = Number(row.value);
    if (!Number.isInteger(value)) throw new Error(`技能 ${id} 的值必须是整数`);
    skills[id] = value;
  }
  const originalInventory = Array.isArray(original.inventory)
    ? original.inventory
    : [];
  const remaining = [...originalInventory];
  const inventory = draft.inventoryText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((label) => {
      const index = remaining.findIndex(
        (item) => inventoryLabel(item) === label,
      );
      return index < 0 ? label : remaining.splice(index, 1)[0];
    });
  // 未能在文本编辑器表示的扩展物品仍保留，不能静默删除。
  inventory.push(...remaining.filter((item) => !inventoryLabel(item)));
  return {
    ...original,
    name: draft.name.trim(),
    occupation: draft.occupation.trim(),
    era: draft.era.trim(),
    age: draft.age.trim() ? Number(draft.age) : null,
    attributes: {
      ...(original.attributes as Record<string, unknown>),
      ...attributes,
    },
    skills,
    credit_rating: credit,
    derived: { ...(original.derived as Record<string, unknown>), LUCK: luck },
    inventory,
    backstory: {
      ...(original.backstory as Record<string, unknown>),
      description: draft.description.trim(),
      background: draft.background.trim(),
      key_connection: draft.keyConnection.trim(),
    },
  };
}

/** jsdom/旧内核没有 Blob.text()：退回 FileReader。 */
async function readFileText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

/** 与服务端同源的推导预览（服务器保存时仍会权威重算）。 */
function derivePreview(attrs: Record<string, string>) {
  const num = (id: string) => Number(attrs[id]) || 0;
  const db = num("STR") + num("SIZ");
  return {
    HP: Math.floor((num("SIZ") + num("CON")) / 10),
    SAN: num("POW"),
    MP: Math.floor(num("POW") / 5),
    DB:
      db < 65
        ? "-2"
        : db < 85
          ? "-1"
          : db < 125
            ? "0"
            : db < 165
              ? "+1D4"
              : "+1D6",
  };
}

function toOption(entry: LibraryEntry): CharacterOption {
  return {
    ref: { source: "library", id: entry.id },
    id: entry.id,
    name: entry.name,
    occupation: entry.occupation,
    age: entry.age ?? null,
    era: entry.era ?? "",
    source_label: "角色库",
    hp: entry.hp,
    max_hp: entry.max_hp,
    san: entry.san,
    max_san: entry.max_san,
    reputation: entry.reputation ?? 0,
    completed_modules: entry.completed_modules ?? 0,
    credit_rating: entry.credit_rating,
    attributes: entry.attributes,
    derived: entry.derived,
    inventory: entry.inventory,
    backstory: entry.backstory,
    top_skills: entry.top_skills,
    description: entry.description,
  };
}

function formatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}

/** 库变更后通知选角页：重推 character_list（本地与云端单人房间同构）。 */
function notifyLibraryChanged(selectId?: string) {
  if (selectId) {
    useStartStore.setState({ pendingLibraryCharacterId: selectId });
  }
  const mode = useAppStore.getState().mode;
  if (mode === "local" || useOnlineStore.getState().activeWorldId) {
    safeSend(JSON.stringify({ type: "character_list" }));
  }
}

export function CharacterLibraryPanel() {
  const open = useAppStore((state) => state.characterLibraryOpen);
  const { rendered, closing } = useDelayedClose(open, 160);
  const [entries, setEntries] = useState<LibraryEntry[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setView] = useState<
    { name: "list" } | { name: "edit"; id: string | null } | { name: "import" }
  >({ name: "list" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  async function reload(selectId?: string) {
    try {
      const list = await listCharacterLibrary();
      setEntries(list);
      setLoadError("");
      if (selectId) setSelectedId(selectId);
      return list;
    } catch (error) {
      setLoadError(
        error instanceof ApiError ? error.message : "读取角色库失败，请重试",
      );
      return null;
    }
  }

  useEffect(() => {
    if (!open) return;
    setView({ name: "list" });
    setConfirmingDeleteId(null);
    setNotice("");
    void reload();
  }, [open]);

  // Escape 关闭（与其它面板一致）；忙碌（保存/导入中）时不打断。
  useEffect(() => {
    if (!open) return;
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!busy) useAppStore.getState().setCharacterLibraryOpen(false);
      }
    };
    document.addEventListener("keydown", listener, true);
    return () => document.removeEventListener("keydown", listener, true);
  }, [open, busy]);

  if (!rendered) return null;

  const close = () => useAppStore.getState().setCharacterLibraryOpen(false);

  const selected = entries?.find((entry) => entry.id === selectedId) ?? null;

  return (
    <div
      className={`character-library-overlay${closing ? " overlay-closing" : ""}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) close();
      }}
    >
      <div
        className="character-library-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="character-library-title"
      >
        <header className="character-library-header">
          <h2 id="character-library-title">角色库</h2>
          <button
            type="button"
            className="panel-close-btn"
            disabled={busy}
            onClick={close}
          >
            关闭
          </button>
        </header>
        {view.name === "list" && (
          <LibraryListView
            entries={entries}
            loadError={loadError}
            notice={notice}
            selected={selected}
            selectedId={selectedId}
            confirmingDeleteId={confirmingDeleteId}
            busy={busy}
            onSelect={setSelectedId}
            onCreate={() => setView({ name: "edit", id: null })}
            onImport={() => setView({ name: "import" })}
            onEdit={(id) => setView({ name: "edit", id })}
            onDuplicate={async (id) => {
              setBusy(true);
              try {
                const clone = await duplicateLibraryEntry(id);
                await reload(clone.id);
                notifyLibraryChanged();
                setNotice(`已复制为「${clone.name}」`);
              } catch (error) {
                setNotice(
                  error instanceof ApiError
                    ? error.message
                    : "复制失败，请重试",
                );
              } finally {
                setBusy(false);
              }
            }}
            onExport={async (entry) => {
              setBusy(true);
              try {
                await exportLibraryEntry(entry.id, entry.name);
                setNotice(`已导出「${entry.name}」`);
              } catch (error) {
                setNotice(
                  error instanceof ApiError
                    ? error.message
                    : "导出失败，请重试",
                );
              } finally {
                setBusy(false);
              }
            }}
            onDelete={async (id) => {
              setBusy(true);
              try {
                await deleteLibraryEntry(id);
                setConfirmingDeleteId(null);
                if (selectedId === id) setSelectedId(null);
                await reload();
                notifyLibraryChanged();
                setNotice("已删除。已开局世界中的同名角色不受影响。");
              } catch (error) {
                setNotice(
                  error instanceof ApiError
                    ? error.message
                    : "删除失败，请重试",
                );
              } finally {
                setBusy(false);
              }
            }}
            onConfirmingDelete={setConfirmingDeleteId}
          />
        )}
        {view.name === "edit" && (
          <LibraryEditorView
            entry={entries?.find((entry) => entry.id === view.id) ?? null}
            busy={busy}
            onCancel={() => setView({ name: "list" })}
            onSave={async (draft) => {
              setBusy(true);
              try {
                const original = view.id ? await getLibraryCard(view.id) : {};
                const payload = draftToCard(draft, original);
                const result = view.id
                  ? await updateLibraryEntry(view.id, payload)
                  : await createLibraryEntry(payload);
                await reload(result.entry.id);
                notifyLibraryChanged(view.id ? undefined : result.entry.id);
                setView({ name: "list" });
                setNotice(
                  [
                    `已保存「${result.entry.name}」`,
                    ...(result.warnings || []),
                  ].join("\n"),
                );
              } catch (error) {
                // 保存失败保留草稿：错误抛回编辑器内联展示，不离开编辑视图。
                if (
                  error instanceof ApiError &&
                  error.code === "invalid_card"
                ) {
                  throw error;
                }
                if (error instanceof ApiError) throw error;
                throw new ApiError("保存失败，请重试", 0, null);
              } finally {
                setBusy(false);
              }
            }}
          />
        )}
        {view.name === "import" && (
          <LibraryImportView
            busy={busy}
            setBusy={setBusy}
            onCancel={() => setView({ name: "list" })}
            onDone={async (entryId, warnings) => {
              await reload(entryId);
              notifyLibraryChanged(entryId);
              setView({ name: "list" });
              setNotice(["导入成功。", ...warnings].join("\n"));
            }}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 列表视图

function LibraryListView({
  entries,
  loadError,
  notice,
  selected,
  selectedId,
  confirmingDeleteId,
  busy,
  onSelect,
  onCreate,
  onImport,
  onEdit,
  onDuplicate,
  onExport,
  onDelete,
  onConfirmingDelete,
}: {
  entries: LibraryEntry[] | null;
  loadError: string;
  notice: string;
  selected: LibraryEntry | null;
  selectedId: string | null;
  confirmingDeleteId: string | null;
  busy: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onImport: () => void;
  onEdit: (id: string) => void;
  onDuplicate: (id: string) => void;
  onExport: (entry: LibraryEntry) => void;
  onDelete: (id: string) => void;
  onConfirmingDelete: (id: string | null) => void;
}) {
  return (
    <div className="library-body">
      <div className="library-list-pane">
        <div className="library-toolbar">
          <button
            type="button"
            className="btn-primary library-toolbar-btn"
            onClick={onCreate}
          >
            新建角色
          </button>
          <button
            type="button"
            className="btn-ghost library-toolbar-btn"
            onClick={onImport}
          >
            导入角色卡
          </button>
        </div>
        {loadError && (
          <p className="online-notice online-notice--error" role="alert">
            {loadError}
          </p>
        )}
        {entries === null && !loadError && (
          <p className="library-empty-hint">正在读取角色库…</p>
        )}
        {entries !== null && entries.length === 0 && (
          <div className="library-empty">
            <p className="library-empty-title">角色库还是空的</p>
            <p className="library-empty-hint">
              新建一个角色，或导入 JSON
              角色卡。这里的角色可以在每次开局时直接选用。
            </p>
          </div>
        )}
        <ul className="library-list">
          {(entries ?? []).map((entry) => (
            <li key={entry.id}>
              <div
                className={`library-row${selectedId === entry.id ? " selected" : ""}`}
              >
                <button
                  type="button"
                  className="library-row-main"
                  onClick={() => onSelect(entry.id)}
                >
                  <span className="library-row-name">{entry.name}</span>
                  <span className="library-row-meta">
                    {entry.occupation || "调查员"} · HP {entry.hp} · SAN{" "}
                    {entry.san}
                  </span>
                  <span className="library-row-time">
                    {formatTime(entry.updated_at)}
                  </span>
                </button>
                <div className="library-row-actions">
                  <button
                    type="button"
                    className="btn-ghost library-row-btn"
                    onClick={() => onEdit(entry.id)}
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    className="btn-ghost library-row-btn"
                    disabled={busy}
                    onClick={() => onDuplicate(entry.id)}
                  >
                    复制
                  </button>
                  <button
                    type="button"
                    className="btn-ghost library-row-btn"
                    disabled={busy}
                    onClick={() => onExport(entry)}
                  >
                    导出
                  </button>
                  <button
                    type="button"
                    className="btn-ghost library-row-btn library-row-delete"
                    disabled={busy}
                    onClick={() => onConfirmingDelete(entry.id)}
                  >
                    删除
                  </button>
                </div>
              </div>
              {confirmingDeleteId === entry.id && (
                <div className="library-delete-confirm" role="group">
                  <span>
                    确认删除「{entry.name}
                    」？已开局世界与历史存档中的该角色不受影响。
                  </span>
                  <button
                    type="button"
                    className="btn-ghost library-row-btn library-row-delete"
                    disabled={busy}
                    onClick={() => onDelete(entry.id)}
                  >
                    确认删除
                  </button>
                  <button
                    type="button"
                    className="btn-ghost library-row-btn"
                    disabled={busy}
                    onClick={() => onConfirmingDelete(null)}
                  >
                    取消
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
        {notice && (
          <p className="library-notice" role="status">
            {notice}
          </p>
        )}
      </div>
      <div className="library-detail-pane">
        {selected ? (
          <CharacterDossier key={selected.id} character={toOption(selected)} />
        ) : (
          <div className="character-detail-empty">选择左侧角色查看档案</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 编辑视图

function LibraryEditorView({
  entry,
  busy,
  onCancel,
  onSave,
}: {
  entry: LibraryEntry | null;
  busy: boolean;
  onCancel: () => void;
  onSave: (draft: EditorDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<EditorDraft>(() =>
    entry ? draftFromEntry(entry) : emptyDraft(),
  );
  const [errors, setErrors] = useState<string[]>([]);
  const derived = useMemo(
    () => derivePreview(draft.attributes),
    [draft.attributes],
  );

  const set = (patch: Partial<EditorDraft>) =>
    setDraft((current) => ({ ...current, ...patch }));

  const submit = async () => {
    setErrors([]);
    try {
      await onSave(draft);
    } catch (error) {
      if (error instanceof ApiError) {
        // 服务端 400 会带字段级 details（角色卡校验契约）
        const details = Array.isArray(error.details)
          ? (error.details as LibraryIssue[]).map(
              (item) => `${item.field}：${item.message}`,
            )
          : [];
        setErrors(details.length ? details : [error.message]);
      } else if (error instanceof Error) {
        setErrors([error.message]);
      }
    }
  };

  return (
    <div className="library-editor">
      <div className="library-editor-grid">
        <label className="library-field">
          <span>姓名 *</span>
          <input
            value={draft.name}
            maxLength={40}
            onChange={(event) => set({ name: event.target.value })}
          />
        </label>
        <label className="library-field">
          <span>职业 *</span>
          <input
            value={draft.occupation}
            maxLength={40}
            onChange={(event) => set({ occupation: event.target.value })}
          />
        </label>
        <label className="library-field">
          <span>年代</span>
          <input
            value={draft.era}
            maxLength={20}
            onChange={(event) => set({ era: event.target.value })}
          />
        </label>
        <label className="library-field">
          <span>年龄</span>
          <input
            value={draft.age}
            inputMode="numeric"
            onChange={(event) => set({ age: event.target.value })}
          />
        </label>
      </div>
      <fieldset className="library-fieldset">
        <legend>属性（保存时由服务端按属性重算 HP/SAN/MP 等）</legend>
        <div className="library-attr-grid">
          {ATTRIBUTE_IDS.map((id) => (
            <label className="library-attr" key={id}>
              <span>{CHARACTER_ATTRIBUTE_LABELS[id] || id}</span>
              <input
                value={draft.attributes[id]}
                inputMode="numeric"
                aria-label={`属性 ${id}`}
                onChange={(event) =>
                  set({
                    attributes: {
                      ...draft.attributes,
                      [id]: event.target.value,
                    },
                  })
                }
              />
            </label>
          ))}
          <label className="library-attr">
            <span>幸运</span>
            <input
              value={draft.luck}
              inputMode="numeric"
              aria-label="幸运 LUCK"
              onChange={(event) => set({ luck: event.target.value })}
            />
          </label>
          <label className="library-attr">
            <span>信用评级</span>
            <input
              value={draft.creditRating}
              inputMode="numeric"
              aria-label="信用评级"
              onChange={(event) => set({ creditRating: event.target.value })}
            />
          </label>
        </div>
        <p className="library-derived-preview">
          推导预览：HP {derived.HP} · SAN {derived.SAN} · MP {derived.MP} · DB{" "}
          {derived.DB}
        </p>
      </fieldset>
      <fieldset className="library-fieldset">
        <legend>技能（id: 数值，如 spot_hidden: 60）</legend>
        {draft.skills.map((row, index) => (
          <div className="library-skill-row" key={index}>
            <input
              value={row.id}
              list="library-skill-options"
              placeholder="技能 id"
              aria-label={`技能 ${index + 1} 名称`}
              onChange={(event) =>
                set({
                  skills: draft.skills.map((item, i) =>
                    i === index ? { ...item, id: event.target.value } : item,
                  ),
                })
              }
            />
            <input
              value={row.value}
              inputMode="numeric"
              aria-label={`技能 ${index + 1} 数值`}
              onChange={(event) =>
                set({
                  skills: draft.skills.map((item, i) =>
                    i === index ? { ...item, value: event.target.value } : item,
                  ),
                })
              }
            />
            <button
              type="button"
              className="btn-ghost library-row-btn"
              aria-label={`移除技能 ${index + 1}`}
              onClick={() =>
                set({ skills: draft.skills.filter((_, i) => i !== index) })
              }
            >
              移除
            </button>
          </div>
        ))}
        <datalist id="library-skill-options">
          {Object.entries(CHARACTER_SKILL_LABELS).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </datalist>
        <button
          type="button"
          className="btn-ghost library-toolbar-btn"
          onClick={() =>
            set({ skills: [...draft.skills, { id: "", value: "50" }] })
          }
        >
          添加技能
        </button>
      </fieldset>
      <fieldset className="library-fieldset">
        <legend>随身物品（每行一件）</legend>
        <textarea
          className="library-textarea"
          rows={3}
          value={draft.inventoryText}
          onChange={(event) => set({ inventoryText: event.target.value })}
        />
      </fieldset>
      <fieldset className="library-fieldset">
        <legend>背景资料（用户资料，仅作叙事参考）</legend>
        <label className="library-field">
          <span>外貌描述</span>
          <textarea
            className="library-textarea"
            rows={2}
            value={draft.description}
            onChange={(event) => set({ description: event.target.value })}
          />
        </label>
        <label className="library-field">
          <span>经历</span>
          <textarea
            className="library-textarea"
            rows={3}
            value={draft.background}
            onChange={(event) => set({ background: event.target.value })}
          />
        </label>
        <label className="library-field">
          <span>关键羁绊</span>
          <textarea
            className="library-textarea"
            rows={2}
            value={draft.keyConnection}
            onChange={(event) => set({ keyConnection: event.target.value })}
          />
        </label>
      </fieldset>
      {errors.length > 0 && (
        <ul className="library-errors" role="alert">
          {errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
      <div className="library-actions">
        <button
          type="button"
          className="btn-primary library-toolbar-btn"
          disabled={busy || !draft.name.trim() || !draft.occupation.trim()}
          onClick={() => void submit()}
        >
          {busy ? "保存中…" : entry ? "保存修改" : "创建角色"}
        </button>
        <button
          type="button"
          className="btn-ghost library-toolbar-btn"
          disabled={busy}
          onClick={onCancel}
        >
          取消
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 导入视图

function LibraryImportView({
  busy,
  setBusy,
  onCancel,
  onDone,
}: {
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onCancel: () => void;
  onDone: (entryId: string, warnings: string[]) => Promise<void>;
}) {
  const [fileError, setFileError] = useState("");
  const [inspecting, setInspecting] = useState(false);
  const [result, setResult] = useState<{
    payload: unknown;
    ok: boolean;
    errors: LibraryIssue[];
    warnings: string[];
    preview: LibraryEntry | null;
  } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const inspectFile = async (file: File) => {
    setFileError("");
    setResult(null);
    if (file.size > MAX_FILE_BYTES) {
      setFileError(
        `文件过大（${Math.ceil(file.size / 1024)} KB），上限 ${MAX_FILE_BYTES / 1024} KB`,
      );
      return;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(await readFileText(file));
    } catch {
      setFileError("文件不是合法的 JSON");
      return;
    }
    setInspecting(true);
    try {
      const inspected = await inspectLibraryCard(payload);
      setResult({
        payload,
        ok: inspected.ok,
        errors: inspected.errors,
        warnings: inspected.warnings,
        preview: inspected.preview ?? null,
      });
    } catch (error) {
      setFileError(
        error instanceof ApiError ? error.message : "校验请求失败，请重试",
      );
    } finally {
      setInspecting(false);
    }
  };

  const confirmImport = async () => {
    if (!result?.ok) return;
    setBusy(true);
    try {
      const created = await createLibraryEntry(result.payload);
      await onDone(created.entry.id, [
        ...(result.warnings || []),
        ...(created.warnings || []),
      ]);
    } catch (error) {
      setFileError(
        error instanceof ApiError ? error.message : "导入失败，请重试",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="library-import">
      <p className="library-empty-hint">
        支持 trpg-character-card v1 格式的 JSON 角色卡（不超过{" "}
        {MAX_FILE_BYTES / 1024} KB）。
        <a href="/examples/character-card.example.json" download>
          下载示例角色卡
        </a>
      </p>
      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void inspectFile(file);
          event.target.value = "";
        }}
      />
      <div className="library-actions">
        <button
          type="button"
          className="btn-primary library-toolbar-btn"
          disabled={busy || inspecting}
          onClick={() => fileRef.current?.click()}
        >
          {inspecting ? "校验中…" : "选择角色卡文件"}
        </button>
        <button
          type="button"
          className="btn-ghost library-toolbar-btn"
          disabled={busy}
          onClick={onCancel}
        >
          返回
        </button>
      </div>
      {fileError && (
        <p className="online-notice online-notice--error" role="alert">
          {fileError}
        </p>
      )}
      {result && !result.ok && (
        <div className="library-import-errors" role="alert">
          <p>无法导入，请先修正以下问题：</p>
          <ul className="library-errors">
            {result.errors.map((issue) => (
              <li key={`${issue.field}:${issue.message}`}>
                <code>{issue.field}</code>：{issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result?.ok && result.preview && (
        <div className="library-import-preview">
          {result.warnings.length > 0 && (
            <ul className="library-warnings">
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          <CharacterDossier character={toOption(result.preview)} />
          <div className="library-actions">
            <button
              type="button"
              className="btn-primary library-toolbar-btn"
              disabled={busy}
              onClick={() => void confirmImport()}
            >
              {busy ? "导入中…" : "确认导入"}
            </button>
            <button
              type="button"
              className="btn-ghost library-toolbar-btn"
              disabled={busy}
              onClick={() => setResult(null)}
            >
              重新选择
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
