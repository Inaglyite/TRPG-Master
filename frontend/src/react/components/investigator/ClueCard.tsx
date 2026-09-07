import { useAppStore, type ClueItem } from "../../../state/app-store";
import { clueSummaryOf } from "../../../investigator-actions";
import {
  useInvestigatorPanelStore,
  useWorldPrefs,
  type ClueFilter,
} from "../../../state/investigator-panel-store";
import { CollapsibleCard } from "./CollapsibleCard";

const KNOWN_CATEGORIES = ["investigation", "event", "task", "npc"] as const;
const CATEGORY_LABELS: Record<string, string> = {
  investigation: "探案线索",
  event: "事件线索",
  task: "任务线索",
  npc: "人物线索",
  other: "其他",
};
const FILTERS: { id: ClueFilter; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "investigation", label: "探案" },
  { id: "event", label: "事件" },
  { id: "task", label: "任务" },
  { id: "npc", label: "人物" },
];

/** 与 panels.ts 的已知线索键同构：分类 + 服务端 id/正文/序号。 */
export function clueKeyOf(category: string, item: ClueItem, index: number) {
  return `${category}:${item.id || item.text || index}`;
}

type GroupedClues = {
  category: string;
  items: { item: ClueItem; key: string }[];
};

/** 分组且保留未知分类：未知分类合并为“其他”组并计入总数。 */
export function groupClues(clues: Record<string, ClueItem[]>): {
  groups: GroupedClues[];
  total: number;
} {
  const groups: GroupedClues[] = [];
  let total = 0;
  const known = new Set<string>(KNOWN_CATEGORIES);
  const push = (category: string, items: { item: ClueItem; key: string }[]) => {
    if (!items.length) return;
    groups.push({ category, items });
    total += items.length;
  };
  for (const category of KNOWN_CATEGORIES) {
    push(
      category,
      (clues[category] || []).map((item, index) => ({
        item,
        key: clueKeyOf(category, item, index),
      })),
    );
  }
  const other = Object.entries(clues)
    .filter(([category]) => !known.has(category))
    .flatMap(([category, items]) =>
      (items || []).map((item, index) => ({
        item,
        key: clueKeyOf(category, item, index),
      })),
    );
  push("other", other);
  return { groups, total };
}

function ClueRow({
  clueKey,
  item,
  isNew,
  expanded,
  onToggleDetail,
  onImage,
  onPresent,
}: {
  clueKey: string;
  item: ClueItem;
  isNew: boolean;
  expanded: boolean;
  onToggleDetail: (key: string) => void;
  onImage: (src: string, alt: string) => void;
  onPresent: (key: string, summary: string, text: string) => void;
}) {
  const summary = clueSummaryOf(item);
  const src = item.asset?.asset_data_uri || item.asset?.asset_url || "";
  const alt = item.asset?.label || item.asset?.file || "线索图片";
  return (
    <div
      className={`inv-clue-row${isNew ? " is-new" : ""}`}
      data-clue={clueKey}
    >
      <div className="inv-clue-main">
        <div className="inv-clue-summary">
          {isNew && <span className="inv-clue-new">新增</span>}
          <span className="inv-clue-summary-text">{summary}</span>
        </div>
        <div className="clue-meta">
          {item.type === "profile" && (
            <span className="clue-tier profile">人物</span>
          )}
          {item.type === "inferred" && (
            <span className="clue-tier inferred">推理</span>
          )}
          {item.asset?.file && <span className="clue-tier asset">图像</span>}
        </div>
        <div
          className={`inv-collapse inv-clue-detail-wrap${expanded ? "" : " closed"}`}
          aria-hidden={!expanded}
        >
          <div className="inv-collapse-clip">
            <div className="inv-clue-detail">{item.text || "（无详情）"}</div>
          </div>
        </div>
      </div>
      <div className="inv-clue-ops">
        {src ? (
          <button
            type="button"
            className="clue-thumb-btn"
            title={alt}
            onClick={() => onImage(src, alt)}
          >
            <img className="clue-thumb" src={src} alt={alt} loading="lazy" />
          </button>
        ) : item.asset?.file ? (
          <div className="clue-thumb-missing" title={item.asset.file}>
            图像不可用
          </div>
        ) : null}
        <button
          type="button"
          className="btn-ghost inv-row-btn inv-clue-detail-btn"
          aria-expanded={expanded}
          onClick={() => onToggleDetail(clueKey)}
        >
          {expanded ? "收起" : "详情"}
        </button>
        <button
          type="button"
          className="btn-ghost inv-row-btn inv-present-btn"
          onClick={() => onPresent(clueKey, summary, item.text || "")}
        >
          出示
        </button>
      </div>
    </div>
  );
}

export function ClueCard({
  onImage,
}: {
  onImage: (src: string, alt: string) => void;
}) {
  const clues = useAppStore((state) => state.clues);
  const prefs = useWorldPrefs();
  const toggleCard = useInvestigatorPanelStore((state) => state.toggleCard);
  const setClueFilter = useInvestigatorPanelStore(
    (state) => state.setClueFilter,
  );
  const setGroupOpen = useInvestigatorPanelStore((state) => state.setGroupOpen);
  const toggleClueDetail = useInvestigatorPanelStore(
    (state) => state.toggleClueDetail,
  );
  const openEditor = useInvestigatorPanelStore((state) => state.openEditor);

  const { groups, total } = groupClues(clues);
  const filter = prefs.clueFilter;
  const visibleGroups =
    filter === "all"
      ? groups
      : groups.filter((group) => group.category === filter);
  const newKeys = new Set(
    prefs.seenInitialized
      ? groups
          .flatMap((group) => group.items)
          .map((entry) => entry.key)
          .filter((key) => !prefs.seenClueKeys.includes(key))
      : [],
  );

  const openPresent = (key: string, summary: string) => {
    openEditor({
      kind: "present",
      clueKey: key,
      clueSummary: summary,
      target: "",
      question: "",
      physicalItem: null,
    });
  };

  return (
    <CollapsibleCard
      cardId="clues"
      title="线索"
      emblem="❧"
      count={total}
      collapsed={prefs.collapsed.clues}
      onToggle={() => toggleCard("clues")}
      summary={`共 ${total} 条`}
    >
      <div
        className="inv-clue-filters"
        role="tablist"
        aria-label="线索分类筛选"
      >
        {FILTERS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={filter === entry.id}
            className={`inv-clue-filter${filter === entry.id ? " active" : ""}`}
            onClick={() => setClueFilter(entry.id)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {/* key=filter：切换筛选时整组重挂载，容器淡入一次 */}
      <div className="inv-clue-groups" key={filter}>
        {total === 0 && <div className="clue-empty">暂无记录</div>}
        {total > 0 && visibleGroups.length === 0 && (
          <div className="clue-empty">该分类暂无线索</div>
        )}

        {visibleGroups.map((group, groupIndex) => {
          // “全部”视图分组可折叠，默认只展开首个非空分类；指定分类直接平铺。
          const collapsible = filter === "all";
          const open = collapsible
            ? (prefs.groupOverrides[group.category] ?? groupIndex === 0)
            : true;
          const rows = (
            <div className="inv-clue-group-items">
              {group.items.map(({ item, key }) => (
                <ClueRow
                  key={key}
                  clueKey={key}
                  item={item}
                  isNew={newKeys.has(key)}
                  expanded={prefs.expandedClues.includes(key)}
                  onToggleDetail={toggleClueDetail}
                  onImage={onImage}
                  onPresent={openPresent}
                />
              ))}
            </div>
          );
          if (!collapsible) return <div key={group.category}>{rows}</div>;
          return (
            <div className="inv-clue-group" key={group.category}>
              <button
                type="button"
                className="inv-clue-group-toggle"
                aria-expanded={open}
                onClick={() => setGroupOpen(group.category, !open)}
              >
                <span className="inv-clue-group-caret" aria-hidden="true">
                  ▾
                </span>
                {CATEGORY_LABELS[group.category] || group.category}
                <span className="inv-clue-group-count">
                  {group.items.length}
                </span>
              </button>
              <div
                className={`inv-collapse inv-clue-group-body${open ? "" : " closed"}`}
                aria-hidden={!open}
              >
                <div className="inv-collapse-clip">{rows}</div>
              </div>
            </div>
          );
        })}
      </div>
    </CollapsibleCard>
  );
}
