import { useEffect, useRef, useState } from "react";
import {
  previewCaseCharacter,
  saveCaseCharacter,
  exportCaseCharacter,
  type LibraryEntry,
} from "../../../api/characterLibrary";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";
import type { CaseSettlement } from "../../../protocol/combat";

/** Explicit account-scoped persistence; it never changes the original card or world. */
export function CaseCharacterActions({ receipt }: { receipt: CaseSettlement }) {
  const identity = useStructuredStore((s) => s.identity);
  const mode = useAppStore((s) => s.mode);
  const userId = useOnlineStore((s) => s.user?.id || "");
  const context = `${mode}:${userId}:${identity.worldId}:${receipt.investigator_id}:${JSON.stringify(receipt.case)}`;
  const epoch = useRef(0);
  const scope = useRef(context);
  scope.current = context;
  const [name, setName] = useState("");
  const [revision, setRevision] = useState(0);
  const [digest, setDigest] = useState("");
  const [busy, setBusy] = useState("preview");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<LibraryEntry | null>(null);
  const current = (token: number) =>
    token === epoch.current && scope.current === context;
  const source = () => ({
    world_id: identity.worldId,
    investigator_id: receipt.investigator_id,
    case_id: receipt.case.case_id,
    expected_revision: revision || identity.revision,
    ...(digest ? { receipt_digest: digest } : {}),
  });
  const load = async () => {
    const token = ++epoch.current;
    setBusy("preview");
    setError("");
    try {
      const data = await previewCaseCharacter(source());
      if (!current(token)) return;
      setRevision(data.revision);
      setDigest(data.receipt_digest);
      setName(
        (n) => data.saved_entry?.name || n || String(data.card.name || ""),
      );
      setSaved(data.saved_entry);
    } catch (e) {
      if (current(token))
        setError(
          e instanceof Error ? e.message : "无法读取结案角色，请重新查看。",
        );
    } finally {
      if (current(token)) setBusy("");
    }
  };
  useEffect(() => {
    setName("");
    setRevision(0);
    setDigest("");
    setSaved(null);
    void load();
    return () => {
      ++epoch.current;
    };
  }, [context]);
  const act = async (kind: "save" | "export") => {
    if (busy || !revision || !name.trim()) return;
    const token = ++epoch.current;
    setBusy(kind);
    setError("");
    try {
      if (kind === "save") {
        const result = await saveCaseCharacter({
          ...source(),
          name: name.trim(),
        });
        if (current(token)) setSaved(result.entry);
      } else
        await exportCaseCharacter(
          { ...source(), name: name.trim() },
          name.trim(),
        );
    } catch (e) {
      if (current(token))
        setError(e instanceof Error ? e.message : "操作未完成，请重试。");
    } finally {
      if (current(token)) setBusy("");
    }
  };
  return (
    <section
      className="case-character-actions"
      data-testid="case-character-actions"
      aria-label="保存角色生涯"
    >
      <h4>保存角色生涯</h4>
      <p>只保存你自己的角色，原卡不会被覆盖。</p>
      <p className="case-character-note">
        生涯、技能、物品与结案状态记录随卡保留；新冒险仍按建卡规则初始化生命等推导值，不代表本场角色被治疗。
      </p>
      {busy === "preview" ? (
        <p role="status">正在核对结案角色…</p>
      ) : (
        <label>
          新角色名
          <input
            value={name}
            maxLength={40}
            disabled={!!busy || !!saved}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      )}
      {saved && (
        <p className="case-character-saved" role="status">
          已保存为新角色「{saved.name}」，可在角色管理中查看。
        </p>
      )}
      <div className="case-character-buttons">
        <button
          type="button"
          className="btn-primary"
          disabled={!!busy || !revision || !!saved || !name.trim()}
          onClick={() => void act("save")}
        >
          {busy === "save" ? "正在保存…" : saved ? "已保存" : "保存为新角色"}
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={!!busy || !revision || !name.trim()}
          onClick={() => void act("export")}
        >
          {busy === "export" ? "正在导出…" : "导出角色卡"}
        </button>
        {error && (
          <button
            type="button"
            className="btn-ghost"
            disabled={!!busy}
            onClick={() => void load()}
          >
            重新查看
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
