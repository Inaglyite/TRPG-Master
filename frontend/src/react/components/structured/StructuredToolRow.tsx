/**
 * StructuredToolRow.tsx — 结构化模式下的游戏工具行：普通「掷骰」入口。
 *
 * 只在世界声明 structured_v1 时出现；legacy 世界不显示，避免改变旧节奏。
 * 协议不可用时按钮禁用并写明原因，不静默退回文字输入。
 */

import { useState } from "react";

import { interactionPath } from "../../../protocol/structured";
import { narrationGuardReason } from "../../../investigator-structured-actions";
import { useStructuredStore } from "../../../state/structured-store";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { RollDialog } from "./StructuredCards";

export function StructuredToolRow() {
  const path = useStructuredStore((state) =>
    interactionPath(state.capabilities),
  );
  const capabilities = useStructuredStore((state) => state.capabilities);
  const protocolNotice = useStructuredStore((state) => state.protocolNotice);
  const investigatorId = useStructuredStore(
    (state) => state.identity.investigatorId,
  );
  const mode = useAppStore((state) => state.mode);
  const connection = useAppStore((state) => state.connection);
  const me = useOnlineStore((state) =>
    state.members.find((member) => member.user_id === state.user?.id),
  );
  const [rollOpen, setRollOpen] = useState(false);

  if (path !== "structured") return null;

  const disabledReason = protocolNotice
    ? protocolNotice
    : mode === "online" && me?.role === "viewer"
      ? "旁观模式只能查看公开叙事，不能掷骰。"
      : !(
            investigatorId ||
            (mode === "online" && me?.investigator?.character_key)
          )
        ? "未控制调查员，无法掷骰。"
        : !capabilities.structuredProtocol
          ? "服务端能力不完整，暂不能提交结构化请求。"
          : !capabilities.freeRoll
            ? "服务端未开放普通掷骰能力。"
            : narrationGuardReason();

  return (
    <div id="structured-tool-row" data-testid="structured-tool-row">
      <button
        type="button"
        className="btn-ghost structured-tool-btn"
        id="btn-free-roll"
        data-testid="btn-free-roll"
        disabled={disabledReason !== null}
        title={disabledReason ?? "普通掷骰（不影响剧情）"}
        aria-label="普通掷骰"
        onClick={() => setRollOpen(true)}
      >
        🎲 掷骰
      </button>
      {disabledReason &&
        (connection !== "connected" ||
          !capabilities.freeRoll ||
          !investigatorId ||
          me?.role === "viewer") && (
          <span className="structured-tool-note">{disabledReason}</span>
        )}
      {rollOpen && <RollDialog onClose={() => setRollOpen(false)} />}
    </div>
  );
}
