import { useLayoutEffect, useRef } from "react";
import { useGamePreferences } from "../../state/game-preferences";
import {
  useReasoningStore,
  type ReasoningEntry,
} from "../../state/reasoning-store";

function ReasoningBlock({
  entry,
  latest,
}: {
  entry: ReasoningEntry;
  latest: boolean;
}) {
  const content = useRef<HTMLPreElement>(null);
  useLayoutEffect(() => {
    if (content.current)
      content.current.scrollTop = content.current.scrollHeight;
  }, [entry.text]);
  return (
    <details className="model-reasoning-block" open={latest}>
      <summary>
        模型思考 · {entry.model}
        <span>
          {entry.status === "running"
            ? "思考中"
            : entry.status === "interrupted"
              ? "已中断"
              : "已结束"}
        </span>
      </summary>
      <pre ref={content}>{entry.text || "等待模型返回思考内容……"}</pre>
      {entry.truncated && <small>展示内容已达到长度上限。</small>}
    </details>
  );
}

export function ReasoningPanel() {
  const show = useGamePreferences((state) => state.showReasoning);
  const entries = useReasoningStore((state) => state.entries);
  if (!show) return null;
  return (
    <section className="model-reasoning-panel" aria-label="模型思考内容">
      {entries.length ? (
        entries.map((entry, index) => (
          <ReasoningBlock
            key={entry.id}
            entry={entry}
            latest={index === entries.length - 1}
          />
        ))
      ) : (
        <p>
          模型尚未返回思考内容。此开关不会自动开启深度思考，多人房间仅主持可见。
        </p>
      )}
    </section>
  );
}
