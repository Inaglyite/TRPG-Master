import type { ReasoningEntry } from "../../state/reasoning-store";

export function InlineReasoning({ entry }: { entry: ReasoningEntry }) {
  const label =
    entry.status === "running"
      ? "正在思考"
      : entry.status === "interrupted"
        ? "思考已中断"
        : "已思考";

  return (
    <details className="inline-reasoning" open>
      <summary>
        {label}
        <span className="reasoning-chevron" aria-hidden="true" />
      </summary>
      {entry.text && <div className="reasoning-text">{entry.text}</div>}
      {entry.truncated && <small>展示内容已达到长度上限。</small>}
    </details>
  );
}
