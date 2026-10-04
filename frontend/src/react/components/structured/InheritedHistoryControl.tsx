import { useEffect, useRef, useState } from "react";
import { loadNarrativeHistory } from "../../../api/structuredHistory";
import { useAppStore } from "../../../state/app-store";
import { useStructuredStore } from "../../../state/structured-store";
import { ArchiveFolderPanel } from "../ArchiveFolderPanel";

/** A separately labelled read-only archive, never restored as live messages. */
export function InheritedHistoryControl() {
  const worldId = useStructuredStore((state) => state.identity.worldId);
  const history = useStructuredStore((state) => state.inheritedHistory);
  const unavailable = useStructuredStore(
    (state) => state.inheritedHistoryUnavailable,
  );
  const incomplete = useStructuredStore(
    (state) => state.inheritedHistoryIncomplete,
  );
  const historyGeneration = useStructuredStore(
    (state) => state.historyGeneration,
  );
  const local = useAppStore((state) => state.mode === "local");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    setOpen(false);
    setBusy(false);
    setError("");
    return () => {
      generation.current += 1;
    };
  }, [worldId, historyGeneration, local]);
  if (!history && !unavailable) return null;
  if (unavailable)
    return (
      <p className="inherited-history-unavailable">
        旧分支未保存分叉前的历史档案，当前时间线仍可正常继续。
      </p>
    );
  const load = async () => {
    if (!history?.next_before_sequence || busy) return;
    const cursor = history.next_before_sequence;
    const attempt = generation.current;
    setBusy(true);
    setError("");
    try {
      const page = await loadNarrativeHistory(
        worldId,
        cursor,
        local,
        "inherited",
      );
      const current = useStructuredStore.getState();
      if (
        generation.current !== attempt ||
        current.identity.worldId !== worldId ||
        current.historyGeneration !== historyGeneration ||
        current.inheritedHistory?.next_before_sequence !== cursor
      )
        return;
      const known = new Set(
        current.inheritedHistory.messages.map((message) => message.message_id),
      );
      useStructuredStore.setState({
        inheritedHistory: {
          messages: [
            ...page.messages.filter(
              (message) => !known.has(message.message_id),
            ),
            ...current.inheritedHistory.messages,
          ],
          next_before_sequence: page.next_before_sequence,
        },
      });
    } catch (reason) {
      if (generation.current === attempt)
        setError(
          reason instanceof Error ? reason.message : "读取失败，请重试。",
        );
    } finally {
      if (generation.current === attempt) setBusy(false);
    }
  };
  return (
    <section className="inherited-history" data-testid="inherited-history">
      <button
        className="btn-ghost"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "收起分叉前历史" : "查看分叉前历史"}
      </button>
      {open && (
        <ArchiveFolderPanel variant="wide" className="inherited-history-body">
          <h3>分叉前的历史档案</h3>
          {incomplete && (
            <p className="inherited-history-note">
              更早的旧分支未保存历史档案，这里仅显示已保存的记录。
            </p>
          )}
          <p className="inherited-history-note">
            分叉时保存的共同经历，只显示你当前有权阅读的内容；不会重执行行动。
          </p>
          {history?.next_before_sequence && (
            <button
              className="btn-ghost"
              type="button"
              disabled={busy}
              onClick={() => void load()}
            >
              {busy ? "正在读取…" : "载入更早的档案"}
            </button>
          )}
          {error && <p role="alert">{error} 可重试读取。</p>}
          {!history?.messages.length && <p>没有你当前可见的分叉前记录。</p>}
          {history?.messages.map((message) => (
            <article
              key={message.message_id}
              className="inherited-history-entry"
            >
              <header>
                {message.speaker.name}
                {message.entry_kind === "action_request" && (
                  <span>行动申报 · 不代表已执行</span>
                )}
              </header>
              <p>{message.text}</p>
            </article>
          ))}
          <p className="inherited-history-note">以下是当前时间线。</p>
        </ArchiveFolderPanel>
      )}
    </section>
  );
}
