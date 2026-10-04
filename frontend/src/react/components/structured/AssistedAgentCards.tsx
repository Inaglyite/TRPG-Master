/**
 * AssistedAgentCards.tsx — assisted 草稿与 agent 控制权反馈。
 *
 * 明确边界（主规格 §9 / §11 M3）：这些能力**按阶段依赖后端**。
 * 只有服务端在 `server_capabilities` 里声明了对应能力、并真的下发了事件时，
 * 卡片才出现；没有对应帧时前端不渲染任何“看似可用”的入口。
 *
 * 草稿批准／拒绝共用 resolve_draft 命令；assisted_draft 标志不代替命令目录。
 * 回执、结算与投影更新分别呈现，不把本地按钮点击或收件当作已执行。
 */

import { useEffect, useState } from "react";
import {
  keeperCommandBlockReason,
  sendKeeperCommand,
} from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";

const CONTROL_TEXTS: Record<string, string> = {
  active: "AI 持有主持权",
  paused: "AI 已暂停",
  takeover: "人类已接管",
  budget_exceeded: "AI 已超预算，等待接管",
  unavailable: "AI 不可用，等待接管",
};

export function AssistedDraftCard() {
  const draft = useStructuredStore((state) => state.keeperDraft);
  const mode = useAppStore((state) => state.mode);
  const connection = useAppStore((state) => state.connection);
  const worldId = useStructuredStore((state) => state.identity.worldId);
  const userId = useOnlineStore((state) => state.user?.id);
  const onlineKeeper = useOnlineStore((state) =>
    state.members.some(
      (member) =>
        member.user_id === state.user?.id && member.can_keeper === true,
    ),
  );
  const authorized = mode === "local" || onlineKeeper;
  const context = `${mode}:${worldId}:${userId ?? "local"}:${authorized}:${draft?.draftId ?? ""}`;
  const [submission, setSubmission] = useState<{
    draftId: string;
    requestId: string;
    context: string;
  } | null>(null);
  const [sendError, setSendError] = useState("");
  useEffect(() => {
    setSubmission(null);
    setSendError("");
  }, [context]);
  const submittedRequest = useStructuredStore((state) =>
    submission?.context === context
      ? state.requests[submission.requestId]
      : null,
  );
  const assisted = useStructuredStore(
    (state) => state.capabilities.assistedDraft,
  );
  const capabilities = useStructuredStore((state) => state.capabilities);
  useStructuredStore((state) => state.protocolNotice);
  useStructuredStore((state) => state.identity.revision);

  if (!authorized || !assisted || !draft) return null;
  const pending =
    submission?.context === context &&
    !submittedRequest?.errorCode &&
    !["failed", "completed", "declined", "cancelled", "paused"].includes(
      submittedRequest?.status ?? "queued",
    );
  const settled = submittedRequest?.status === "completed";
  // Subscribe to capability/protocol/revision changes, not only draft identity.
  const blocked = keeperCommandBlockReason("resolve_draft");
  const blockedNote = !capabilities.commands.includes("resolve_draft")
    ? "服务端未开放草稿审批，不能提交批准或拒绝。"
    : blocked;
  const feedback =
    sendError ||
    submittedRequest?.errorMessage ||
    (settled
      ? "服务端已结算，等待草稿列表更新。"
      : pending
        ? submittedRequest?.serverReceived
          ? "服务端已收件，等待审批结算。"
          : "审批请求已发送，尚未确认执行结果。"
        : submission?.context === context
          ? submittedRequest?.detail || "草稿处理未完成，请确认当前状态后重试。"
          : "");
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
    if (pending || settled || blocked) return;
    if (
      useStructuredStore.getState().identity.worldId !== worldId ||
      useStructuredStore.getState().keeperDraft?.draftId !== draft.draftId
    )
      return;
    const result = sendKeeperCommand("resolve_draft", {
      draft_id: draft.draftId,
      decision,
      ...(draft.note ? { note: draft.note } : {}),
    });
    if (result.ok) {
      setSubmission({
        draftId: draft.draftId,
        requestId: result.requestId,
        context,
      });
      setSendError("");
    } else setSendError(result.reason);
  };

  return (
    <article
      className="structured-card keeper-draft-card"
      data-testid="keeper-draft-card"
      aria-live="polite"
    >
      <header className="structured-card-head">
        <span className="structured-card-title">主持草稿（仅主持可见）</span>
        <span className="structured-badge">
          {settled ? "已结算" : pending ? "审批中" : "待批准"}
        </span>
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
          disabled={pending || settled || blocked !== null}
          aria-description={blockedNote ?? undefined}
          title="批准并执行这条草稿（resolve_draft: approved）"
          onClick={() => resolve("approved")}
        >
          批准并执行
        </button>
        <button
          type="button"
          className="btn-ghost structured-btn"
          data-testid="draft-reject"
          disabled={pending || settled || blocked !== null}
          aria-description={blockedNote ?? undefined}
          title="拒绝这条草稿（resolve_draft: rejected）"
          onClick={() => resolve("rejected")}
        >
          拒绝草稿
        </button>
      </div>
      {connection !== "connected" && (
        <p className="structured-card-note">
          正在恢复连接与权限，草稿尚未执行。
        </p>
      )}
      {blocked && connection === "connected" && (
        <p className="structured-card-note">{blockedNote}</p>
      )}
      {feedback && (
        <p className="structured-card-note" role="status">
          {feedback}
        </p>
      )}
    </article>
  );
}

export function KeeperControlNotice() {
  const control = useStructuredStore((state) => state.keeperControl);
  const mode = useAppStore((state) => state.mode);
  const connection = useAppStore((state) => state.connection);
  const userId = useOnlineStore((state) => state.user?.id);
  const onlineKeeper = useOnlineStore((state) =>
    state.members.some(
      (m) => m.user_id === state.user?.id && m.can_keeper === true,
    ),
  );
  const keeperMode = useStructuredStore((state) => state.identity.keeperMode);
  const worldId = useStructuredStore((state) => state.identity.worldId);
  const supported = useStructuredStore((state) =>
    state.capabilities.commands.includes("control_keeper"),
  );
  const requests = useStructuredStore((state) => state.requests);
  const authorized = mode === "local" || onlineKeeper;
  const context = `${mode}:${worldId}:${userId ?? "local"}:${authorized}`;
  const [submission, setSubmission] = useState<{
    context: string;
    requestId: string;
  } | null>(null);
  const [sendError, setSendError] = useState("");
  useEffect(() => {
    setSubmission(null);
    setSendError("");
  }, [context]);
  const request =
    submission?.context === context
      ? requests[submission.requestId]
      : undefined;
  const pending =
    submission?.context === context &&
    !request?.errorCode &&
    !["completed", "failed", "declined", "cancelled", "paused"].includes(
      request?.status ?? "queued",
    );
  const feedback =
    sendError ||
    (submission?.context === context
      ? request?.errorMessage ||
        (request?.status === "completed"
          ? "服务端已确认主持操作。"
          : ["failed", "declined", "cancelled", "paused"].includes(
                request?.status ?? "",
              )
            ? request?.detail || "本次操作未完成，可重新提交。"
            : request?.serverReceived
              ? "服务端已收件，等待本次主持操作结算。"
              : "操作已发送，等待收件确认。")
      : "");
  const canControl = supported && authorized;
  const ownControl =
    control?.controllerKind === "human" &&
    control.controllerId === (mode === "local" ? "local" : userId);
  const aiMode = keeperMode === "agent" || keeperMode === "assisted";
  // 控制权身份与 can_keeper 授权分开。快照或暂停信息不能给玩家自封主持。
  if (!authorized) return null;
  if ((!control || control.state === "active") && !canControl) return null;
  const submit = (action: string, requestId?: string) => {
    if (pending || connection !== "connected") return;
    const result = sendKeeperCommand("control_keeper", {
      action,
      ...(requestId ? { request_id: requestId } : {}),
    });
    if (result.ok) {
      setSubmission({ context, requestId: result.requestId });
      setSendError("");
    } else setSendError(result.reason);
  };

  const title = ownControl
    ? "你正在主持"
    : control?.controllerKind === "human"
      ? "另一位人类主持持有控制权"
      : keeperMode === "human"
        ? "人类主持"
        : control && control.state !== "active"
          ? CONTROL_TEXTS[control.state]
          : control?.controllerKind === "agent"
            ? "AI 持有主持权"
            : aiMode
              ? "后续行动交由 AI 处理"
              : "主持控制";
  const detail =
    control?.detail === "human_takeover" || control?.detail === "returned_to_ai"
      ? ""
      : control?.detail;

  return (
    <div
      className="structured-card structured-control-notice"
      data-testid="keeper-control-notice"
      data-tone={control?.state === "unavailable" ? "warn" : undefined}
      role="status"
    >
      <header className="structured-card-head">
        <span className="structured-card-title">{title}</span>
        <span className="structured-control-private">仅主持可见</span>
      </header>
      {keeperMode === "human" && (
        <p className="structured-card-note">
          本场由人类主持，不需要配置模型；控制权不等于房主身份。
        </p>
      )}
      {detail && <p className="structured-card-note">{detail}</p>}
      {!supported && control && (
        <p className="structured-card-note">
          服务端未开放主持控制操作，当前状态仅供查看。
        </p>
      )}
      {connection !== "connected" && (
        <p className="structured-card-note">连接恢复后才能提交主持操作。</p>
      )}
      {aiMode &&
        Object.values(requests).some(
          (r) =>
            r.kind !== "command" &&
            (r.status === "paused" || r.status === "failed"),
        ) && (
          <p className="structured-card-note">
            重试会把主持权交还
            AI，并继续处理指定待办；此前已结算的行动不会回滚。
          </p>
        )}
      {canControl && (
        <div className="structured-card-actions">
          {!ownControl && (
            <button
              type="button"
              className="btn-ghost structured-btn"
              disabled={!!pending || connection !== "connected"}
              onClick={() => submit("take")}
            >
              接管主持
            </button>
          )}
          {aiMode && ownControl && (
            <button
              type="button"
              className="btn-ghost structured-btn"
              disabled={!!pending || connection !== "connected"}
              onClick={() => submit("release")}
            >
              交还 AI 主持
            </button>
          )}
          {aiMode &&
            (control?.controllerKind !== "human" || ownControl) &&
            Object.values(requests)
              .filter(
                (r) =>
                  r.kind !== "command" &&
                  (r.status === "paused" || r.status === "failed"),
              )
              .map((r) => (
                <button
                  type="button"
                  key={r.requestId}
                  className="btn-ghost structured-btn"
                  title={`交还 AI 主持并继续处理：${r.label}（已结算行动不会回滚）`}
                  disabled={!!pending || connection !== "connected"}
                  onClick={() => submit("retry", r.requestId)}
                >
                  交还 AI 并重试：
                  <span className="structured-control-target">{r.label}</span>
                </button>
              ))}
        </div>
      )}
      {feedback && <p className="structured-card-note">{feedback}</p>}
    </div>
  );
}
