import { useEffect, useRef, useState } from "react";
import { loadStructuredAsset } from "../../../api/structuredAssets";
import { HandoutImageViewer } from "../HandoutImageViewer";

export type ReceivedMaterial = { id: string; label: string };

/** The caller supplies a recipient-only server projection, never keeper assets. */
export function ReceivedMaterials({
  entries,
  worldId,
  scope,
  local,
}: {
  entries: readonly ReceivedMaterial[];
  worldId: string;
  scope: string;
  local: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [selection, setSelection] = useState<{
    id: string;
    scope: string;
    attempt: number;
  } | null>(null);
  const [result, setResult] = useState<{
    id: string;
    scope: string;
    attempt: number;
    source: string;
    error: boolean;
  } | null>(null);
  const returnFocus = useRef<HTMLButtonElement>(null);
  const allowed =
    selection !== null &&
    selection.scope === scope &&
    entries.some((entry) => entry.id === selection.id);
  const entry = allowed
    ? entries.find((item) => item.id === selection.id)
    : null;
  const currentResult =
    allowed &&
    result?.id === selection.id &&
    result.scope === scope &&
    result.attempt === selection.attempt
      ? result
      : null;

  useEffect(() => {
    if (selection && !allowed) setSelection(null);
  }, [selection, allowed]);

  useEffect(() => {
    if (!selection || !allowed || !worldId) return;
    let active = true;
    const requested = selection;
    void loadStructuredAsset(worldId, requested.id, local)
      .then((asset) => {
        if (!active) return;
        if (asset.asset_id !== requested.id) throw new Error("wrong material");
        setResult({ ...requested, source: asset.asset_data_uri, error: false });
      })
      .catch(() => {
        if (active) setResult({ ...requested, source: "", error: true });
      });
    return () => {
      active = false;
    };
  }, [selection, allowed, worldId, local]);

  if (!entries.length) return null;
  return (
    <section className="inv-received-materials" aria-label="收到的图片">
      <button
        type="button"
        className="inv-clue-group-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        收到的图片{" "}
        <span className="inv-clue-group-count">{entries.length}</span>
      </button>
      {expanded && (
        <>
          <p className="inv-path-note">
            这里仅列出主持已给你的图片。查看不会执行行动。
          </p>
          <ul className="inv-received-material-list">
            {entries.map((item) => (
              <li key={item.id}>
                <span>{item.label}</span>
                <button
                  type="button"
                  className="btn-ghost inv-row-btn"
                  aria-label={`查看图片：${item.label}`}
                  disabled={
                    !worldId || (entry?.id === item.id && !currentResult)
                  }
                  onClick={(event) => {
                    returnFocus.current = event.currentTarget;
                    setSelection({ id: item.id, scope, attempt: 0 });
                    setResult(null);
                  }}
                >
                  查看
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {entry && !currentResult && (
        <div className="inv-received-recovery">
          <p role="status">正在读取“{entry.label}”…</p>
          <button
            type="button"
            className="btn-ghost inv-row-btn"
            onClick={() => setSelection(null)}
          >
            取消读取图片
          </button>
        </div>
      )}
      {entry && currentResult?.error && (
        <div role="alert">
          <p>图片未能读取，可能连接中断或查看权限已变化。</p>
          <button
            type="button"
            className="btn-ghost inv-row-btn"
            onClick={() =>
              setSelection((value) =>
                value ? { ...value, attempt: value.attempt + 1 } : null,
              )
            }
          >
            重新读取图片
          </button>
          <button
            type="button"
            className="btn-ghost inv-row-btn"
            onClick={() => setSelection(null)}
          >
            取消查看
          </button>
        </div>
      )}
      {entry && currentResult?.source && (
        <HandoutImageViewer
          source={currentResult.source}
          label={entry.label}
          returnFocus={returnFocus}
          onClose={() => setSelection(null)}
        />
      )}
    </section>
  );
}
