/**
 * AssistedAgentCards.tsx — assisted 草稿与 agent 控制权反馈。
 *
 * 明确边界（主规格 §9 / §11 M3）：这些能力**按阶段依赖后端**。
 * 只有服务端在 `server_capabilities` 里声明了对应能力、并真的下发了事件时，
 * 卡片才出现；没有对应帧时前端不渲染任何“看似可用”的入口。
 *
 * 已知缺口如实呈现：M0 没有定义草稿拒绝帧，因此“拒绝”按钮只有在服务端
 * 显式声明 `reject_supported` 时才可点，否则写明原因而不假装能拒绝。
 */

import { useState } from "react";
import { sendKeeperCommand } from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";

const CONTROL_TEXTS: Record<string, string> = {
  active: "Agent 正在主持",
  paused: "Agent 已暂停",
  takeover: "人类已接管",
  budget_exceeded: "Agent 已超预算，等待接管",
  unavailable: "Agent 不可用，等待接管",
};

export function AssistedDraftCard() {
  const draft = useStructuredStore((state) => state.keeperDraft);
  const [submission, setSubmission] = useState<{
    draftId: string;
    requestId: string;
  } | null>(null);
  const submittedRequest = useStructuredStore((state) =>
    submission ? state.requests[submission.requestId] : null,
  );
  const assisted = useStructuredStore(
    (state) => state.capabilities.assistedDraft,
  );

  if (!assisted || !draft) return null;
  const pending =
    submission?.draftId === draft.draftId &&
    !submittedRequest?.errorCode &&
    !["failed", "completed", "declined", "cancelled"].includes(
      submittedRequest?.status ?? "queued",
    );
  const commands = draft.commands?.length
    ? draft.commands
    : draft.command
      ? [draft.command]
      : [];

  /**
   * 处理草稿走 M1 的 `resolve_draft`：approved / rejected / edited。
   * 批准后由服务端按草稿内容执行，前端不再另行提交那一条命令。
   */
  const resolve = (decision: "approved" | "rejected" | "edited") => {
    const result = sendKeeperCommand("resolve_draft", {
      draft_id: draft.draftId,
      decision,
      ...(draft.note ? { note: draft.note } : {}),
    });
    if (result.ok)
      setSubmission({ draftId: draft.draftId, requestId: result.requestId });
  };

  return (
    <article
      className="structured-card keeper-draft-card"
      data-testid="keeper-draft-card"
      aria-live="polite"
    >
      <header className="structured-card-head">
        <span className="structured-card-title">主持草稿（仅你可见）</span>
        <span className="structured-badge">待批准</span>
      </header>
      <p className="structured-card-detail">{draft.summary}</p>
      {commands.map((command, index) => (
        <p key={index} className="structured-card-note">
          将执行：{command.kind}（{JSON.stringify(command.payload)}）
        </p>
      ))}
      {draft.narration && (
        <p className="structured-card-detail">叙事草稿：{draft.narration}</p>
      )}
      <div className="structured-card-actions">
        <button
          type="button"
          className="btn-primary structured-btn"
          data-testid="draft-approve"
          disabled={pending}
          title="批准并执行这条草稿（resolve_draft: approved）"
          onClick={() => resolve("approved")}
        >
          批准并执行
        </button>
        <button
          type="button"
          className="btn-ghost structured-btn"
          data-testid="draft-reject"
          disabled={pending}
          title="拒绝这条草稿（resolve_draft: rejected）"
          onClick={() => resolve("rejected")}
        >
          拒绝草稿
        </button>
      </div>
    </article>
  );
}

export function KeeperControlNotice() {
  const control = useStructuredStore((state) => state.keeperControl);
  const takeover = useStructuredStore(
    (state) => state.capabilities.agentTakeover,
  );
  const mode = useAppStore((state) => state.mode);
  const onlineKeeper = useOnlineStore((state) =>
    state.members.some(
      (m) => m.user_id === state.user?.id && m.can_keeper === true,
    ),
  );
  const keeperMode = useStructuredStore((state) => state.identity.keeperMode);
  const supported = useStructuredStore((state) =>
    state.capabilities.commands.includes("control_keeper"),
  );
  const requests = useStructuredStore((state) => state.requests);
  const [feedback, setFeedback] = useState("");
  const canControl = supported && (mode === "local" || onlineKeeper);
  if ((!control || control.state === "active") && !canControl) return null;
  const submit = (action: string, requestId?: string) => {
    const result = sendKeeperCommand("control_keeper", {
      action,
      ...(requestId ? { request_id: requestId } : {}),
    });
    setFeedback(result.ok ? "操作已提交，等待服务端确认。" : result.reason);
  };

  return (
    <div
      className="structured-card structured-control-notice"
      data-testid="keeper-control-notice"
      data-tone={control?.state === "unavailable" ? "warn" : undefined}
      role="status"
    >
      <div className="structured-card-title">主持控制</div>
      {control && (
        <p>
          {CONTROL_TEXTS[control.state] ?? control.state}
          {control.detail ? `：${control.detail}` : ""}
          {!takeover &&
            control.state !== "takeover" &&
            "（服务端未开放接管入口）"}
        </p>
      )}
      {canControl && (
        <div className="structured-card-actions">
          <button
            className="btn-ghost structured-btn"
            onClick={() => submit("take")}
          >
            接管主持
          </button>
          {keeperMode !== "human" && (
            <button
              className="btn-ghost structured-btn"
              onClick={() => submit("release")}
            >
              交还 AI 主持
            </button>
          )}
          {keeperMode !== "human" &&
            Object.values(requests)
              .filter(
                (r) =>
                  r.kind !== "command" &&
                  (r.status === "paused" || r.status === "failed"),
              )
              .map((r) => (
                <button
                  key={r.requestId}
                  className="btn-ghost structured-btn"
                  onClick={() => submit("retry", r.requestId)}
                >
                  重试：{r.label.slice(0, 18)}
                </button>
              ))}
        </div>
      )}
      {feedback && <p>{feedback}</p>}
    </div>
  );
}
