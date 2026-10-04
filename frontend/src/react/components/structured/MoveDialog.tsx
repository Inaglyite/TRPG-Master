/**
 * MoveDialog.tsx — 「前往…」入口。
 *
 * 只列出服务端公开投影的目的地（`session_snapshot.destinations`），
 * 不泄露完整秘密地图；点击目的地就是明确提出现在出发，提交 `move` 结构请求。
 * 位置仍然只由服务端 `scene_changed` 事件更新。
 */

import { useState } from "react";

import {
  sendStructuredAction,
  structuredPlayerRequestReason,
} from "../../../structured-transport";
import {
  structuredUnavailableReason,
  type InteractionPath,
} from "../../../protocol/structured";
import { currentPanelPath } from "../../../investigator-panel-view";
import { narrationGuardReason } from "../../../investigator-structured-actions";
import { useStructuredStore } from "../../../state/structured-store";
import { useOnlineStore } from "../../../state/online-store";
import { sceneLabel, useSceneStore } from "../../../state/scene-store";
import { CompactGameDialog } from "../CompactGameDialog";

export function MoveDialog({ onClose }: { onClose: () => void }) {
  const destinations = useStructuredStore((state) => state.destinations);
  const capabilities = useStructuredStore((state) => state.capabilities);
  const protocolNotice = useStructuredStore((state) => state.protocolNotice);
  useStructuredStore((state) => state.identity);
  useOnlineStore((state) => state.members);
  useOnlineStore((state) => state.user);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState<string>("");
  const currentScene = useSceneStore(sceneLabel);

  const path: InteractionPath = currentPanelPath();
  const blocked =
    structuredPlayerRequestReason() ??
    narrationGuardReason() ??
    structuredUnavailableReason(capabilities, protocolNotice);

  const submit = (destinationId: string) => {
    setSending(destinationId);
    try {
      const result = sendStructuredAction({
        kind: "move",
        destination_scene_id: destinationId,
      });
      if (result.ok) {
        onClose();
        return;
      }
      setError(result.reason);
    } finally {
      setSending("");
    }
  };

  return (
    <CompactGameDialog
      id="move-panel"
      title="前往…"
      closeLabel="关闭前往面板"
      onClose={onClose}
      context={
        <p className="compact-dialog-location">当前场景 · {currentScene}</p>
      }
      footer={
        <button
          type="button"
          className="btn-ghost panel-action-cancel"
          onClick={onClose}
        >
          取消
        </button>
      }
    >
      <p className="panel-action-note">
        提出出发请求，由守秘人回应；抵达不等于调查，也不会自动取得线索或结算
        SAN。
      </p>
      {blocked && (
        <p className="panel-action-error" role="alert">
          {blocked}
        </p>
      )}
      {path === "structured" && !blocked && destinations.length === 0 && (
        <p className="clue-empty" data-testid="move-empty">
          服务端尚未提供公开目的地投影；结构化模式下不会用正文里的地名代替。
        </p>
      )}
      <ul className="structured-destination-list">
        {destinations.map((destination) => (
          <li key={destination.id}>
            <button
              type="button"
              className="btn-ghost structured-destination"
              data-scene-id={destination.id}
              disabled={blocked !== null || sending !== ""}
              title={blocked ?? `出发前往${destination.name}`}
              onClick={() => submit(destination.id)}
            >
              <span className="structured-destination-name">
                {destination.name}
              </span>
              <span className="structured-destination-arrow" aria-hidden="true">
                →
              </span>
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p className="panel-action-error" role="alert">
          {error}
        </p>
      )}
    </CompactGameDialog>
  );
}
