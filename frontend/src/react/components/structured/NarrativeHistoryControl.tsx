import { useEffect, useRef, useState } from "react";
import { loadNarrativeHistory } from "../../../api/structuredHistory";
import { prependNarrativeHistory } from "../../../structured-history";
import { useAppStore } from "../../../state/app-store";
import { useStructuredStore } from "../../../state/structured-store";

export function NarrativeHistoryControl() {
  const worldId = useStructuredStore((state) => state.identity.worldId);
  const cursor = useStructuredStore((state) => state.historyBeforeSequence);
  const historyGeneration = useStructuredStore(
    (state) => state.historyGeneration,
  );
  const local = useAppStore((state) => state.mode === "local");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    setBusy(false);
    setError("");
    return () => {
      generation.current += 1;
    };
  }, [worldId, cursor, historyGeneration]);
  if (cursor === null) return null;
  const load = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    const attempt = generation.current;
    try {
      const page = await loadNarrativeHistory(worldId, cursor, local);
      const current = useStructuredStore.getState();
      if (
        generation.current !== attempt ||
        current.identity.worldId !== worldId ||
        current.historyGeneration !== historyGeneration ||
        current.historyBeforeSequence !== cursor
      )
        return;
      prependNarrativeHistory(worldId, page);
      useStructuredStore.setState({
        historyBeforeSequence: page.next_before_sequence,
      });
    } catch (reason: unknown) {
      if (generation.current === attempt)
        setError(
          reason instanceof Error ? reason.message : "读取失败，请重试。",
        );
    } finally {
      if (generation.current === attempt) setBusy(false);
    }
  };
  return (
    <div
      className="structured-history-control"
      data-testid="structured-history-control"
    >
      <button
        type="button"
        className="btn-ghost"
        disabled={busy}
        onClick={() => void load()}
      >
        {busy ? "正在读取叙事…" : "载入更早叙事"}
      </button>
      <span>只读取已提交、你有权看到的叙事，不重执行行动。</span>
      {error && <p role="alert">{error} 可重试读取。</p>}
    </div>
  );
}
