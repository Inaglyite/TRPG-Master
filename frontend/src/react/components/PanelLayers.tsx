import { useEffect, useState } from "react";

import * as panels from "../../panels";
import {
  useAppStore,
  type Handout,
  type SaveEntry,
} from "../../state/app-store";
import { CharacterPanelContent } from "./CharacterPanelContent";

function panelCommand(name: string, ...args: unknown[]) {
  const command = (panels as Record<string, (...values: any[]) => void>)[name];
  command?.(...args);
}

export function CharacterPanel() {
  const open = useAppStore((state) => state.characterPanelOpen);
  return (
    <aside id="char-panel" className={open ? "" : "collapsed"}>
      <div id="char-content">
        <CharacterPanelContent />
      </div>
    </aside>
  );
}

function HandoutCard({ handout }: { handout: Handout }) {
  const dismiss = useAppStore((state) => state.dismissHandout);
  const [expanded, setExpanded] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const close = () => {
    if (leaving) return;
    setLeaving(true);
    window.setTimeout(() => dismiss(handout.id), 280);
  };
  useEffect(() => {
    const timer = window.setTimeout(close, 10000);
    return () => window.clearTimeout(timer);
  });
  const source = handout.asset_data_uri || handout.asset_url;
  return (
    <>
      <div className={`handout-card${leaving ? " leaving" : ""}`}>
        <div className="handout-header">
          <span className="handout-label">{handout.label || handout.file}</span>
          <button className="handout-close" onClick={close}>
            ✕
          </button>
        </div>
        <img
          src={source}
          alt={handout.label || handout.file}
          loading="lazy"
          onClick={() => setExpanded(true)}
        />
      </div>
      {expanded && (
        <div
          className="handout-overlay"
          role="presentation"
          onClick={() => setExpanded(false)}
        >
          <img src={source} alt={handout.label || handout.file} />
        </div>
      )}
    </>
  );
}

export function HandoutLayer() {
  const handouts = useAppStore((state) => state.handouts);
  const clueToast = useAppStore((state) => state.clueToast);
  return (
    <>
      <div id="handout-container">
        {handouts.map((handout) => (
          <HandoutCard key={handout.id} handout={handout} />
        ))}
      </div>
      {clueToast && (
        <div id="toast-stack">
          <div className="clue-toast">{clueToast}</div>
        </div>
      )}
    </>
  );
}

function formatSaveTime(createdAt?: string) {
  if (!createdAt) return { absolute: "未知时间", relative: "" };
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime()))
    return { absolute: "未知时间", relative: "" };
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  const relative =
    minutes < 1
      ? "刚刚"
      : minutes < 60
        ? `${minutes}分钟前`
        : minutes < 1440
          ? `${Math.floor(minutes / 60)}小时前`
          : `${Math.floor(minutes / 1440)}天前`;
  return {
    absolute: date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }),
    relative,
  };
}

function SaveRow({ save, latest }: { save: SaveEntry; latest: boolean }) {
  const renameSlotId = useAppStore((state) => state.renameSlotId);
  const [name, setName] = useState(save.label || save.scene_name || "");
  const renaming = renameSlotId === save.id;
  const isAuto = save.id === "slot_000";
  const time = formatSaveTime(save.created_at);
  return (
    <div
      className={`save-slot-entry${latest ? " save-latest" : ""}`}
      data-slot={save.id}
    >
      <div className="save-slot-info">
        <div className="save-slot-title">
          {renaming ? (
            <div className="save-rename-form">
              <input
                autoFocus
                className="save-rename-input"
                maxLength={50}
                value={name}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter")
                    void panelCommand("renameSave", save.id, name);
                  if (event.key === "Escape")
                    useAppStore.setState({ renameSlotId: null });
                }}
              />
              <button
                className="save-rename-confirm"
                onClick={() => void panelCommand("renameSave", save.id, name)}
              >
                ✓
              </button>
              <button
                className="save-rename-cancel"
                onClick={() => useAppStore.setState({ renameSlotId: null })}
              >
                ×
              </button>
            </div>
          ) : (
            <span className="save-slot-name">
              {latest && (
                <>
                  <span className="save-badge">最新</span>{" "}
                </>
              )}
              {isAuto ? "💾" : "📁"}{" "}
              {save.label || save.scene_name || "未知场景"}
            </span>
          )}
          <span className="save-slot-time">
            {time.relative} · {time.absolute}
          </span>
        </div>
        <div className="save-slot-meta">
          <span>{save.scene_name || "未知场景"}</span>
          <span>{save.character_name || "未知调查员"}</span>
          <span>
            HP {String(save.hp ?? "?")} SAN {String(save.san ?? "?")}
          </span>
          <span>📜 {save.clue_count ?? 0} 线索</span>
          <span>💬 {save.message_count ?? 0} 消息</span>
        </div>
      </div>
      <div className="save-slot-actions">
        <button
          className="save-action-load"
          aria-label="读取存档"
          onClick={() => void panelCommand("loadSave", save.id)}
        >
          📂
        </button>
        <button
          className="save-action-rename"
          aria-label="重命名存档"
          onClick={() => useAppStore.setState({ renameSlotId: save.id })}
        >
          ✏️
        </button>
        {!isAuto && (
          <button
            className="save-action-del"
            aria-label="删除存档"
            onClick={() => void panelCommand("deleteSave", save.id)}
          >
            🗑
          </button>
        )}
      </div>
    </div>
  );
}

export function SavePanel() {
  const open = useAppStore((state) => state.savePanelOpen);
  const mode = useAppStore((state) => state.savePanelMode);
  const saves = useAppStore((state) => state.saves);
  const worlds = useAppStore((state) => state.worlds);
  const activeWorldId = useAppStore((state) => state.activeWorldId);
  if (!open) return <div id="save-panel-overlay" className="hidden" />;
  return (
    <div
      id="save-panel-overlay"
      data-mode={mode}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget)
          void panelCommand("closeSavePanel");
      }}
    >
      <div id="save-panel">
        <div id="save-panel-header">
          <h3 id="save-panel-title">
            {mode === "load" ? "从存档开始" : "💾 存档管理"}
          </h3>
          <button
            id="save-panel-close"
            className="panel-close-btn"
            onClick={() => void panelCommand("closeSavePanel")}
          >
            关闭
          </button>
        </div>
        <div id="save-panel-body">
          {worlds.length > 0 && (
            <section
              id="world-panel-section"
              aria-labelledby="world-panel-heading"
            >
              <div id="world-panel-heading">时间线</div>
              <div id="world-panel-list">
                {worlds.map((world) => {
                  const id = String(world.world_id || "");
                  const active = Boolean(world.active) || id === activeWorldId;
                  return (
                    <div
                      className={`world-entry${active ? " active" : ""}`}
                      key={id}
                    >
                      <div className="world-entry-info">
                        <div className="world-entry-title">
                          {world.label ||
                            (world.is_branch ? "时间线分支" : "主时间线")}
                        </div>
                        <div className="world-entry-meta">
                          {world.scene_name || "未知场景"} ·{" "}
                          {world.character_name || "未知调查员"}
                        </div>
                      </div>
                      <button
                        className="world-switch-button"
                        disabled={active}
                        aria-label={
                          active
                            ? "当前时间线"
                            : `切换到${world.label || "此时间线"}`
                        }
                        onClick={() => void panelCommand("switchWorld", id)}
                      >
                        {active ? "当前" : "↪"}
                      </button>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
          {mode !== "load" && (
            <button
              id="save-panel-new"
              className="save-new-btn"
              onClick={() => void panelCommand("createSave")}
            >
              ➕ 新建存档
            </button>
          )}
          <div id="save-panel-list">
            {saves.length ? (
              saves.map((save, index) => (
                <SaveRow key={save.id} save={save} latest={index === 0} />
              ))
            ) : (
              <div className="save-empty">暂无存档</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
