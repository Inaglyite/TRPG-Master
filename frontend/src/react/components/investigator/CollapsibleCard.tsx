import { type ReactNode } from "react";

/**
 * 三卡片共用的可折叠容器：标题、徽记、摘要与收起控件。
 * 纯展示组件，不含任何业务状态；折叠不产生游戏回合。
 */
export function CollapsibleCard({
  cardId,
  title,
  emblem,
  count,
  summary,
  collapsed,
  onToggle,
  children,
}: {
  cardId: string;
  title: string;
  emblem?: string;
  count?: number | null;
  /** 折叠时仍可见的摘要（如 HP/SAN 或数量）。 */
  summary?: ReactNode;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const bodyId = `inv-card-body-${cardId}`;
  return (
    <section
      className={`inv-card inv-card-${cardId}${collapsed ? " collapsed" : ""}`}
      aria-labelledby={`inv-card-title-${cardId}`}
    >
      <div className="inv-card-header">
        <button
          type="button"
          id={`inv-card-toggle-${cardId}`}
          className="inv-card-toggle"
          aria-expanded={!collapsed}
          aria-controls={bodyId}
          onClick={onToggle}
        >
          <span className="inv-card-emblem" aria-hidden="true">
            {emblem}
          </span>
          <span className="inv-card-title" id={`inv-card-title-${cardId}`}>
            {title}
          </span>
          {count != null && <span className="inv-card-count">{count}</span>}
          <span className="inv-card-chevron" aria-hidden="true">
            ▾
          </span>
        </button>
        {summary && <div className="inv-card-summary">{summary}</div>}
      </div>
      {/* 折叠走 grid-rows 补间：body 常挂 DOM，closed 时 0 高 + visibility 隐藏
          （visibility 同时把内容移出焦点序列与无障碍树）。 */}
      <div
        className={`inv-collapse inv-card-body${collapsed ? " closed" : ""}`}
        id={bodyId}
        aria-hidden={collapsed}
      >
        <div className="inv-collapse-clip">{children}</div>
      </div>
    </section>
  );
}
