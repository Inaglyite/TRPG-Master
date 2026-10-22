import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getPlayerTools,
  organizeTurn,
  type DigestItem,
  type PlayerTools,
} from "../../api/playerTools";
import { currentTurnSource, digestNote, mapLayout } from "../../player-tools";
import { sendPlayerText } from "../../options";
import { interactionPath } from "../../protocol/structured";
import { sendStructuredAction } from "../../structured-transport";
import { useAppStore } from "../../state/app-store";
import { useMessageStore } from "../../state/message-store";
import { useOnlineStore } from "../../state/online-store";
import { useSceneStore } from "../../state/scene-store";
import { useStructuredStore } from "../../state/structured-store";
import { useSaveLoadStore } from "../../state/save-load-store";
import { requestNotes, saveNotes } from "../../utility";
import { useDialogKeyboard } from "./useDialogKeyboard";
import "../../styles/components/player-action-toolbar.css";

type Tool = "skill" | "clues" | "map";
const LABELS: Record<Tool, string> = {
  skill: "使用技能",
  clues: "整理线索",
  map: "场景移动",
};
const ICONS: Record<Tool, string> = { skill: "✦", clues: "✎", map: "⌖" };

export function PlayerActionToolbar({
  canAct,
  draft,
  onActionSent,
}: {
  canAct: boolean;
  draft: string;
  onActionSent: () => void;
}) {
  const app = useAppStore();
  const online = useOnlineStore();
  const structured = useStructuredStore();
  const sceneName = useSceneStore((state) => state.name);
  const messages = useMessageStore((state) => state.messages);
  const restoring = useSaveLoadStore((state) => state.progress !== null);
  const completedTurn =
    interactionPath(structured.capabilities) === "legacy"
      ? app.latestBranchTurnId
      : null;
  const source = useMemo(
    () => currentTurnSource(messages, completedTurn),
    [messages, completedTurn],
  );
  const worldId =
    (app.mode === "online" ? online.activeWorldId : app.activeWorldId) || "";
  const actor =
    app.mode === "online"
      ? online.members.find((member) => member.user_id === online.user?.id)
          ?.investigator?.character_key
      : app.character?.name;
  const scope = `${app.mode}/${worldId}/${online.authOrigin}/${online.user?.id || "local"}/${structured.identity.investigatorId || actor || ""}`;
  const readable =
    !!worldId &&
    app.connection === "connected" &&
    (app.mode !== "online" ||
      (online.roomConnection === "connected" && online.roomSnapshotReady));
  const [tool, setTool] = useState<Tool | null>(null);
  const [projection, setProjection] = useState<PlayerTools | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");
  const [approach, setApproach] = useState("");
  const [items, setItems] = useState<(DigestItem & { selected: boolean })[]>(
    [],
  );
  const [excerpt, setExcerpt] = useState<NonNullable<typeof source> | null>(
    null,
  );
  const [added, setAdded] = useState(false);
  const sequence = useRef(0);
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const panel = useRef<HTMLElement>(null);
  const close = useCallback(() => {
    sequence.current++;
    setTool(null);
    setLoading(false);
  }, []);
  useDialogKeyboard(panel, tool !== null, false, close);
  useEffect(() => {
    close();
    setProjection(null);
  }, [scope, close]);
  useEffect(() => {
    if (restoring) close();
  }, [restoring, close]);
  useEffect(
    () => () => {
      sequence.current++;
    },
    [],
  );

  const loadProjection = useCallback(() => {
    const id = ++sequence.current;
    const requestScope = scope;
    setLoading(true);
    setProjection(null);
    setError("");
    void getPlayerTools(worldId, app.mode === "local")
      .then((data) => {
        if (id !== sequence.current || requestScope !== liveScope.current)
          return;
        if (data.world_id !== worldId)
          throw new Error("世界已变化，请重新打开工具。");
        setProjection(data);
        setLoading(false);
      })
      .catch((failure: unknown) => {
        if (id !== sequence.current || requestScope !== liveScope.current)
          return;
        setError(
          failure instanceof Error ? failure.message : "暂时无法读取，请重试。",
        );
        setLoading(false);
      });
  }, [scope, worldId, app.mode]);

  // Refresh a map if another participant moves while it is open.
  useEffect(() => {
    if (tool === "map") loadProjection();
  }, [
    tool,
    sceneName,
    structured.currentSceneId,
    structured.identity.revision,
    app.latestBranchTurnId,
    loadProjection,
  ]);

  const generate = (from: NonNullable<typeof source>) => {
    const id = ++sequence.current;
    const requestScope = scope;
    setLoading(true);
    setError("");
    setItems([]);
    setAdded(false);
    void organizeTurn(worldId, app.mode === "local", from)
      .then((data) => {
        if (id !== sequence.current || requestScope !== liveScope.current)
          return;
        if (data.world_id !== worldId || data.source_id !== from.id)
          throw new Error("整理结果来源已变化，请重新整理。");
        setItems(data.items.map((item) => ({ ...item, selected: true })));
        setLoading(false);
      })
      .catch((failure: unknown) => {
        if (id !== sequence.current || requestScope !== liveScope.current)
          return;
        setError(
          failure instanceof Error ? failure.message : "暂时无法整理，请重试。",
        );
        setLoading(false);
      });
  };

  const open = (next: Tool) => {
    setTool(next);
    setSelected("");
    setSearch("");
    setApproach(draft);
    setError("");
    setAdded(false);
    if (next === "clues" && source) {
      setExcerpt(source);
      requestNotes();
      if (source.text.length <= 12000) generate(source);
      else {
        setItems([]);
        setLoading(false);
        setError("本轮文字较长，可先手动摘录需要保留的部分。");
      }
    } else if (next === "skill") loadProjection();
  };

  const submit = () => {
    if (!canAct || !readable || !projection || liveScope.current !== scope)
      return;
    let result;
    if (tool === "skill") {
      const skill = projection.skills.find((entry) => entry.id === selected);
      if (!skill || !approach.trim()) return;
      result = sendPlayerText(
        `我尝试使用「${skill.name}」，${approach.trim()}`,
      );
    } else {
      const node = projection.map.nodes.find((entry) => entry.id === selected);
      if (!node || node.current) return;
      result =
        interactionPath(structured.capabilities) === "structured"
          ? sendStructuredAction(
              { kind: "move", destination_scene_id: node.id },
              `前往${node.name}`,
            )
          : sendPlayerText(
              `我准备前往「${node.name}」，根据眼下的情况处理出发和抵达。`,
            );
    }
    if (!result.ok) {
      setError(result.reason || "行动未能发出，请重试。");
      return;
    }
    if (tool === "skill") onActionSent();
    close();
  };

  const appendNotes = () => {
    if (
      !excerpt ||
      !readable ||
      liveScope.current !== scope ||
      app.notesLoading ||
      !app.notesLoaded ||
      app.notesSaving
    )
      return;
    if (!added) {
      const chosen = items.filter((item) => item.selected && item.text.trim());
      if (!chosen.length) return;
      const block = digestNote(excerpt, chosen);
      const current = useAppStore.getState();
      const text = current.notesText.includes(block)
        ? current.notesText
        : `${current.notesText.trimEnd()}${current.notesText.trim() ? "\n\n" : ""}${block}`;
      if (text.length > 20000) {
        setError("笔记已接近容量上限，请先整理已有笔记再添加。");
        return;
      }
      current.setNotesDraft(text);
      setAdded(true);
    }
    saveNotes();
  };
  const layout = projection ? mapLayout(projection.map) : null;
  const skills =
    projection?.skills
      .filter((skill) =>
        `${skill.name} ${skill.id}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      )
      .sort(
        (a, b) => b.value - a.value || a.name.localeCompare(b.name, "zh-CN"),
      ) || [];
  const selectedNode = projection?.map.nodes.find(
    (node) => node.id === selected,
  );

  return (
    <>
      <div
        className="player-action-toolbar"
        role="group"
        aria-label="调查行动工具"
      >
        {(["skill", "clues", "map"] as const).map((entry) => (
          <button
            key={entry}
            type="button"
            className="btn-ghost player-tool-button"
            disabled={
              !readable ||
              (entry !== "map" && (!canAct || (entry === "clues" && !source)))
            }
            onClick={() => open(entry)}
            aria-haspopup="dialog"
          >
            <span aria-hidden="true">{ICONS[entry]}</span>
            {LABELS[entry]}
          </button>
        ))}
      </div>
      {tool && (
        <div
          className="player-tool-overlay"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) close();
          }}
        >
          <section
            className={`player-tool-dialog player-tool-dialog--${tool}`}
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-labelledby="player-tool-title"
            tabIndex={-1}
          >
            <header>
              <div>
                <span className="player-tool-eyebrow">
                  INVESTIGATOR / FIELD TOOLS
                </span>
                <h2 id="player-tool-title">{LABELS[tool]}</h2>
              </div>
              <button
                type="button"
                className="btn-ghost player-tool-close"
                aria-label={`关闭${LABELS[tool]}`}
                onClick={close}
              >
                ✕
              </button>
            </header>
            <div className="player-tool-body" data-dialog-scroll>
              {tool === "skill" && (
                <>
                  <p className="player-tool-help">
                    选择调查员技能，再说明你想怎么做。由主持判断是否需要检定。
                  </p>
                  {projection && (
                    <p className="player-tool-character">
                      {projection.character_name || "当前未控制调查员"}
                    </p>
                  )}
                  <input
                    className="player-tool-search"
                    aria-label="搜索技能"
                    placeholder="搜索技能名称…"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                  <div
                    className="player-skill-grid"
                    role="group"
                    aria-label="调查员技能"
                  >
                    {skills.map((skill) => (
                      <button
                        key={skill.id}
                        type="button"
                        className={`btn-ghost player-skill${selected === skill.id ? " is-selected" : ""}`}
                        aria-pressed={selected === skill.id}
                        onClick={() => setSelected(skill.id)}
                      >
                        <span>{skill.name}</span>
                        <strong>{skill.value}%</strong>
                      </button>
                    ))}
                  </div>
                  {!loading && projection && !skills.length && (
                    <p className="player-tool-help">没有符合条件的可用技能。</p>
                  )}
                  <label className="player-tool-field">
                    <span>行动目标与做法</span>
                    <textarea
                      rows={3}
                      maxLength={1000}
                      value={approach}
                      onChange={(event) => setApproach(event.target.value)}
                      placeholder="例如：检查桌边的划痕，判断桌子是否刚被挪动过。"
                    />
                  </label>
                </>
              )}
              {tool === "map" && (
                <>
                  <p className="player-tool-help">
                    只绘出队伍已经抵达的地点。连线表示地点之间的已知连接，不代表实际距离。
                  </p>
                  {layout && layout.nodes.length > 0 ? (
                    <div className="player-map" aria-label="已探索场景地图">
                      <svg
                        viewBox={`0 0 ${layout.width} ${layout.height}`}
                        aria-hidden="true"
                        preserveAspectRatio="none"
                      >
                        {projection?.map.edges.map((edge) => {
                          const a = layout.nodes.find(
                            (node) => node.id === edge.from,
                          );
                          const b = layout.nodes.find(
                            (node) => node.id === edge.to,
                          );
                          return a && b ? (
                            <line
                              key={`${a.id}/${b.id}`}
                              x1={a.x}
                              y1={a.y}
                              x2={b.x}
                              y2={b.y}
                            />
                          ) : null;
                        })}
                      </svg>
                      <div
                        className="player-map-nodes"
                        style={{
                          gridTemplateColumns: `repeat(${Math.min(3, Math.ceil(Math.sqrt(layout.nodes.length)))}, minmax(0, 1fr))`,
                        }}
                      >
                        {layout.nodes.map((node) => (
                          <button
                            key={node.id}
                            type="button"
                            className={`btn-ghost player-map-node${node.current ? " is-current" : ""}${selected === node.id ? " is-selected" : ""}`}
                            aria-pressed={selected === node.id}
                            disabled={node.current}
                            onClick={() => setSelected(node.id)}
                          >
                            <span>{node.name}</span>
                            <small>
                              {node.current ? "当前位置" : "已抵达"}
                            </small>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    !loading && (
                      <p className="player-tool-help">
                        暂时没有可显示的探索记录。
                      </p>
                    )
                  )}
                  {selectedNode && !selectedNode.current && (
                    <p className="player-tool-selection">
                      目的地：{selectedNode.name}
                    </p>
                  )}
                  <p className="player-tool-help">
                    提出移动后，主持会先处理眼下的待决事项。
                  </p>
                </>
              )}
              {tool === "clues" && (
                <>
                  <p className="player-tool-help">
                    从{excerpt?.label || "本轮叙述"}
                    中提炼，可修改、勾选后加入私人调查笔记。
                  </p>
                  <div className="player-digest-list">
                    {items.map((item, index) => (
                      <div className="player-digest-item" key={index}>
                        <label>
                          <input
                            type="checkbox"
                            aria-label={`保留第${index + 1}条摘录`}
                            checked={item.selected}
                            disabled={added}
                            onChange={(event) =>
                              setItems((rows) =>
                                rows.map((row, i) =>
                                  i === index
                                    ? { ...row, selected: event.target.checked }
                                    : row,
                                ),
                              )
                            }
                          />
                          <span>摘录 {index + 1}</span>
                        </label>
                        <textarea
                          aria-label={`摘录${index + 1}内容`}
                          rows={2}
                          maxLength={12000}
                          value={item.text}
                          disabled={added}
                          onChange={(event) =>
                            setItems((rows) =>
                              rows.map((row, i) =>
                                i === index
                                  ? { ...row, text: event.target.value }
                                  : row,
                              ),
                            )
                          }
                        />
                        <blockquote>{item.quote}</blockquote>
                      </div>
                    ))}
                  </div>
                  {!loading && !items.length && !error && (
                    <p className="player-tool-help">
                      本轮没有需要单独记录的信息，也可以手动摘录。
                    </p>
                  )}
                  {!loading && !added && excerpt && (
                    <button
                      type="button"
                      className="btn-ghost player-tool-secondary"
                      onClick={() => {
                        sequence.current++;
                        setItems([
                          {
                            text: excerpt.text,
                            quote: excerpt.text.slice(0, 600),
                            selected: true,
                          },
                        ]);
                        setError("");
                      }}
                    >
                      手动摘录本轮文字
                    </button>
                  )}
                  {!loading &&
                    !added &&
                    excerpt &&
                    excerpt.text.length <= 12000 && (
                      <button
                        type="button"
                        className="btn-ghost player-tool-secondary"
                        disabled={!canAct || !readable}
                        onClick={() => generate(excerpt)}
                      >
                        重新自动整理
                      </button>
                    )}
                  {added && (
                    <p className="player-tool-note-status" role="status">
                      {app.notesStatus || "已加入笔记草稿"}
                    </p>
                  )}
                  {app.notesLoading && (
                    <p className="player-tool-help" role="status">
                      正在读取已有笔记…
                    </p>
                  )}
                  {app.notesStatusKind === "error" && (
                    <p className="player-tool-error" role="alert">
                      {app.notesStatus}
                    </p>
                  )}
                  {app.notesStatusKind === "error" && !added && (
                    <button
                      type="button"
                      className="btn-ghost player-tool-secondary"
                      disabled={app.notesLoading}
                      onClick={requestNotes}
                    >
                      重新读取笔记
                    </button>
                  )}
                  {added && !app.notesDirty && !app.notesSaving && (
                    <button
                      type="button"
                      className="btn-ghost player-tool-secondary"
                      onClick={() => {
                        close();
                        app.setUtilityOpen(true);
                      }}
                    >
                      打开调查笔记
                    </button>
                  )}
                </>
              )}
              {loading && (
                <p className="player-tool-loading" role="status">
                  <span className="player-tool-spinner" aria-hidden="true" />
                  {tool === "clues" ? "正在整理本轮线索…" : "正在读取…"}
                </p>
              )}
              {error && (
                <p className="player-tool-error" role="alert">
                  {error}
                </p>
              )}
              {error && tool !== "clues" && (
                <button
                  type="button"
                  className="btn-ghost player-tool-secondary"
                  onClick={loadProjection}
                >
                  重新读取
                </button>
              )}
            </div>
            <footer>
              <button type="button" className="btn-ghost" onClick={close}>
                关闭
              </button>
              {tool === "clues" ? (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={
                    loading ||
                    !readable ||
                    app.notesLoading ||
                    !app.notesLoaded ||
                    app.notesSaving ||
                    (!added && app.notesStatusKind === "error") ||
                    (added && !app.notesDirty) ||
                    !items.some((item) => item.selected && item.text.trim())
                  }
                  onClick={appendNotes}
                >
                  {app.notesSaving
                    ? "保存中…"
                    : added
                      ? app.notesDirty
                        ? "重试保存笔记"
                        : "已保存"
                      : "加入调查笔记"}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={
                    !canAct ||
                    !readable ||
                    loading ||
                    !projection ||
                    !selected ||
                    (tool === "skill"
                      ? !approach.trim()
                      : !selectedNode || selectedNode.current)
                  }
                  onClick={submit}
                >
                  {tool === "skill" ? "提交技能行动" : "提出移动"}
                </button>
              )}
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
