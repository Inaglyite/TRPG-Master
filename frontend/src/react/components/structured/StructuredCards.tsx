/**
 * StructuredCards.tsx — 玩家侧的结构化操作卡片。
 *
 * - ActionStatusCard：待提交 → 处理中 → 等待回应 → 已完成/未执行/已取消/暂停。
 *   **“服务器收到了请求”不是“行动成功”**：领域结果单独显示 outcome。
 * - CheckRequestCard：持久检定卡；只有被指定的调查员能点“掷骰/放弃”，
 *   其他人只读。参数（技能/难度/奖惩骰/代价）全部来自服务端请求。
 * - RollDock：普通掷骰入口的结果提示与待处理请求列表容器。
 */

import { useMemo, useState } from "react";

import {
  freeRollReason,
  type InteractionThread,
} from "../../../protocol/structured";
import { reopenStructuredEditor } from "../../../investigator-structured-actions";
import {
  resendStructuredRequest,
  resubmitWithFreshRevision,
  sendFreeRoll,
} from "../../../structured-transport";
import { currentStructuredIdentity } from "../../../structured-transport";
import {
  activeRequests,
  openInteractions,
  pendingChecksFor,
  visibleRequestCards,
  visibleChecks,
  useStructuredStore,
  type CheckRequestState,
  type PendingRequest,
} from "../../../state/structured-store";

import { AssistedDraftCard, KeeperControlNotice } from "./AssistedAgentCards";

const STATUS_LABELS: Record<string, string> = {
  queued: "已提交，等待服务端确认",
  processing: "守秘人处理中",
  awaiting_player: "等你回应",
  completed: "已处理完成",
  declined: "已被拒绝",
  cancelled: "已取消",
  paused: "已暂停（可恢复）",
  failed: "处理失败",
};

const OUTCOME_LABELS: Record<string, string> = {
  success: "结果：成功",
  failure: "结果：失败",
  not_executed: "结果：未执行",
};

const DIFFICULTY_LABELS: Record<string, string> = {
  regular: "常规",
  hard: "困难",
  extreme: "极难",
};

export function ActionStatusCard({
  request,
  suppressAwaiting = false,
}: {
  request: PendingRequest;
  /** 同一条待办已由「当前交互」卡展示时为 true：不再重复渲染 awaiting 明细。 */
  suppressAwaiting?: boolean;
}) {
  const [retrying, setRetrying] = useState(false);
  const status = STATUS_LABELS[request.status] ?? request.status;
  const outcome = request.outcome
    ? (OUTCOME_LABELS[request.outcome] ?? request.outcome)
    : null;
  const awaiting =
    request.status === "awaiting_player" && !suppressAwaiting
      ? request.awaiting
      : null;
  const awaitingSummary =
    awaiting?.note ||
    (awaiting?.destinationSceneId
      ? `前往 ${awaiting.destinationSceneId}`
      : awaiting?.target || "");

  return (
    <article
      className="structured-card action-status-card"
      data-status={request.status}
      aria-live="polite"
    >
      <header className="structured-card-head">
        <span className="structured-card-title">{request.label}</span>
        <span
          className={`structured-badge structured-badge--${request.status}`}
        >
          {status}
        </span>
      </header>
      {awaiting && (
        <div className="structured-awaiting" data-testid="structured-awaiting">
          <p className="structured-card-note" data-tone="info">
            守秘人正在等你回应。上面是正常叙事，你的位置与行动都还没有变化。
          </p>
          {awaitingSummary && (
            <p className="structured-card-detail">
              尚未执行：{awaitingSummary}
            </p>
          )}
          {awaiting.disclosed.length > 0 && (
            <p className="structured-card-detail">
              已告知：{awaiting.disclosed.join("；")}
            </p>
          )}
          <p className="structured-card-hint">
            直接说话回应就行：追问、改主意，或让他照办都可以；不需要说“继续”之类的口令。
          </p>
        </div>
      )}
      {request.awaitingAck && (
        <p className="structured-card-note" data-tone="warn">
          还没有收到服务端确认，正在查询原请求状态；重试会沿用同一 request_id，
          不会重复结算。
        </p>
      )}
      {request.errorMessage && (
        <p className="structured-card-error" role="alert">
          {request.errorMessage}
        </p>
      )}
      {outcome && <p className="structured-card-note">{outcome}</p>}
      {request.detail && (
        <p className="structured-card-detail">{request.detail}</p>
      )}
      {(request.awaitingAck || request.errorCode) && (
        <div className="structured-card-actions">
          <button
            type="button"
            className="btn-ghost structured-btn"
            disabled={retrying}
            title="按原请求 ID 重新发送，服务端按幂等键返回已保存的结果"
            onClick={() => {
              setRetrying(true);
              try {
                resendStructuredRequest(request.requestId);
              } finally {
                setRetrying(false);
              }
            }}
          >
            {retrying ? "重发中…" : "重试（同一请求 ID）"}
          </button>
          {request.errorCode === "revision_conflict" && (
            <button
              type="button"
              className="btn-primary structured-btn"
              data-testid="structured-resubmit"
              title="世界已经前进：用最新版本号与新请求 ID 重新提交同一意图"
              onClick={() => {
                const result = resubmitWithFreshRevision(request.requestId);
                if (result === null) resendStructuredRequest(request.requestId);
              }}
            >
              用最新版本重新提交
            </button>
          )}
          {(request.kind === "present_clue" || request.kind === "use_item") && (
            <button
              type="button"
              className="btn-ghost structured-btn"
              data-testid="structured-reopen"
              title="用原来的请求内容重新打开编辑器，改完再提交"
              onClick={() => reopenStructuredEditor(request)}
            >
              重新编辑
            </button>
          )}
        </div>
      )}
    </article>
  );
}

const INTERACTION_STATUS_LABELS: Record<string, string> = {
  open: "进行中",
  completed: "已结束",
  cancelled: "已取消",
  superseded: "已被后续替代",
};

/**
 * M5「当前交互」卡：服务端 interaction_updated / 快照 interactions[] 的公开投影。
 * 只展示玩家可知的目标与「尚未执行」，**不是执行授权**，也没有强制确认按钮——
 * 玩家照常自由说话、追问、改计划；是否出发由主持命令决定。
 */
export function InteractionCard({ thread }: { thread: InteractionThread }) {
  const summary =
    thread.pendingAction.note ||
    (thread.pendingAction.destinationSceneId
      ? `前往 ${thread.pendingAction.destinationSceneId}`
      : thread.pendingAction.target || "");
  return (
    <article
      className="structured-card action-status-card"
      data-status="awaiting_player"
      data-testid="structured-interaction-card"
      aria-live="polite"
    >
      <header className="structured-card-head">
        <span className="structured-card-title">当前交互</span>
        <span className="structured-badge structured-badge--awaiting_player">
          {INTERACTION_STATUS_LABELS[thread.status] ?? thread.status}
        </span>
      </header>
      <div className="structured-awaiting">
        <p className="structured-card-note" data-tone="info">
          守秘人正在等你回应。位置与行动都还没有变化。
        </p>
        {summary && (
          <p className="structured-card-detail">尚未执行：{summary}</p>
        )}
        {thread.disclosed.length > 0 && (
          <p className="structured-card-detail">
            已告知：{thread.disclosed.join("；")}
          </p>
        )}
        <p className="structured-card-hint">
          直接说话回应就行：追问、改主意，或让他照办都可以；不需要说“继续”之类的口令。
        </p>
      </div>
    </article>
  );
}

export function CheckRequestCard({
  check,
  canRespond,
  onRespond,
}: {
  check: CheckRequestState;
  canRespond: boolean;
  onRespond: (decision: "roll" | "decline") => void;
}) {
  const pending = check.status === "pending";
  const result = check.result;
  const modifier =
    check.bonusPenalty === 0
      ? "无"
      : check.bonusPenalty > 0
        ? `奖励骰 ×${check.bonusPenalty}`
        : `惩罚骰 ×${Math.abs(check.bonusPenalty)}`;

  return (
    <article
      className="structured-card check-request-card"
      data-status={check.status}
      aria-live="polite"
    >
      <header className="structured-card-head">
        <span className="structured-card-title">
          检定请求 · {check.skill || "技能"}
        </span>
        <span className={`structured-badge structured-badge--${check.status}`}>
          {pending
            ? "等待掷骰"
            : result?.outcome === "declined"
              ? "已放弃"
              : "已结算"}
        </span>
      </header>
      <dl className="structured-facts">
        <div>
          <dt>调查员</dt>
          <dd>{check.investigatorId || "—"}</dd>
        </div>
        <div>
          <dt>难度</dt>
          <dd>
            {DIFFICULTY_LABELS[check.difficulty] ?? check.difficulty ?? "—"}
          </dd>
        </div>
        <div>
          <dt>奖惩骰</dt>
          <dd>{modifier}</dd>
        </div>
      </dl>
      {check.attempt && (
        <p className="structured-card-detail">尝试：{check.attempt}</p>
      )}
      {check.knownCost && (
        <p className="structured-card-note">已知代价：{check.knownCost}</p>
      )}
      {result && (
        <p className="structured-card-detail">
          {result.roll !== null && result.targetValue !== null
            ? `${result.roll} vs ${result.targetValue}`
            : result.detail}
          {result.roll !== null && result.targetValue !== null && result.detail
            ? `｜${result.detail}`
            : ""}
        </p>
      )}
      {pending && (
        <div className="structured-card-actions">
          <button
            type="button"
            className="btn-primary structured-btn"
            disabled={!canRespond}
            title={
              canRespond
                ? "由服务端结算一次，参数来自这条检定请求"
                : "这条检定由其他调查员响应，你只能查看"
            }
            onClick={() => onRespond("roll")}
          >
            掷骰
          </button>
          <button
            type="button"
            className="btn-ghost structured-btn"
            disabled={!canRespond}
            title={canRespond ? "放弃这次检定" : "这条检定由其他调查员响应"}
            onClick={() => onRespond("decline")}
          >
            放弃
          </button>
        </div>
      )}
    </article>
  );
}

/** 抽屉区：assisted 草稿 / agent 状态 / 待检定 / 待处理请求。挂在聊天区上方。 */
export function StructuredDock() {
  // zustand selector 必须返回稳定引用：派生数组在 useMemo 里算，
  // 否则每次 store 通知都会拿到新数组，触发无限重渲染。
  const requestRecords = useStructuredStore((state) => state.requests);
  const requestOrder = useStructuredStore((state) => state.requestOrder);
  const checkRecords = useStructuredStore((state) => state.checks);
  const checkOrder = useStructuredStore((state) => state.checkOrder);
  const investigatorId = useStructuredStore(
    (state) => state.identity.investigatorId,
  );
  const protocolNotice = useStructuredStore((state) => state.protocolNotice);
  const keeperDraft = useStructuredStore((state) => state.keeperDraft);
  const keeperControl = useStructuredStore((state) => state.keeperControl);
  const unknownEventTypes = useStructuredStore(
    (state) => state.unknownEventTypes,
  );
  const interactionRecords = useStructuredStore((state) => state.interactions);
  const interactionOrder = useStructuredStore(
    (state) => state.interactionOrder,
  );

  const requests = useMemo(
    // 卡片要能显示终态（已完成/被拒绝），所以这里用最近窗口而不是仅未决请求。
    () => visibleRequestCards({ requests: requestRecords, requestOrder }),
    [requestRecords, requestOrder],
  );
  const responses = useMemo(
    () => visibleChecks({ checks: checkRecords, checkOrder }),
    [checkRecords, checkOrder],
  );
  const interactions = useMemo(
    () =>
      openInteractions({ interactions: interactionRecords, interactionOrder }),
    [interactionRecords, interactionOrder],
  );
  // 已被「当前交互」卡覆盖的请求：其 awaiting 明细不再重复渲染一份。
  const coveredRequestIds = useMemo(() => {
    const ids = new Set<string>();
    for (const thread of interactions) {
      if (thread.status !== "open") continue;
      if (thread.originRequestId) ids.add(thread.originRequestId);
      if (thread.lastRequestId) ids.add(thread.lastRequestId);
    }
    return ids;
  }, [interactions]);
  const coveredByThread = (requestId: string) =>
    coveredRequestIds.has(requestId);
  // 草稿与 Agent 控制权状态也要能单独触发渲染（否则暂停提示会被吞掉）。
  if (
    requests.length === 0 &&
    responses.length === 0 &&
    interactions.length === 0 &&
    unknownEventTypes.length === 0 &&
    !protocolNotice &&
    keeperDraft === null &&
    keeperControl === null
  ) {
    return null;
  }

  return (
    <section
      className="structured-dock"
      data-testid="structured-dock"
      aria-label="结构化操作待办"
    >
      {unknownEventTypes.length > 0 && (
        <p
          className="structured-card-note"
          data-testid="structured-unknown-events"
        >
          收到未适配的结构化事件：{unknownEventTypes.join("、")}
          （前端待按冻结协议适配；内容不会被静默丢弃）
        </p>
      )}
      {protocolNotice && (
        <p className="structured-card-error" role="alert">
          {protocolNotice}
        </p>
      )}
      <KeeperControlNotice />
      <AssistedDraftCard />
      {responses.map((check) => (
        <CheckRequestCard
          key={check.checkRequestId}
          check={check}
          canRespond={
            Boolean(investigatorId) && check.investigatorId === investigatorId
          }
          onRespond={(decision) => {
            void import("../../../structured-transport").then((module) =>
              module.sendCheckResponse(check.checkRequestId, decision),
            );
          }}
        />
      ))}
      {interactions.map((thread) => (
        <InteractionCard key={thread.threadId} thread={thread} />
      ))}
      {requests.map((request) => (
        <ActionStatusCard
          key={request.requestId}
          request={request}
          // 同一条待办已经由「当前交互」卡展示时，不再重复渲染 awaiting 块
          suppressAwaiting={coveredByThread(request.requestId)}
        />
      ))}
    </section>
  );
}

/** 普通掷骰面板：受限表达式 + 明确“不触发剧情效果”。 */
export function RollDialog({ onClose }: { onClose: () => void }) {
  const [spec, setSpec] = useState("1d100");
  const [error, setError] = useState<string | null>(null);
  const identity = currentStructuredIdentity();
  const invalid = freeRollReason(spec);

  const submit = () => {
    const result = sendFreeRoll(spec);
    if (result.ok) {
      onClose();
      return;
    }
    setError(result.reason);
  };

  return (
    <div id="roll-panel-overlay" className="structured-overlay-inline">
      <div
        id="roll-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="roll-panel-title"
      >
        <header className="panel-action-header">
          <h3 id="roll-panel-title">普通掷骰</h3>
          <button
            type="button"
            className="btn-ghost panel-action-close"
            aria-label="关闭掷骰面板"
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        <div className="panel-action-body">
          <label className="panel-action-field">
            <span>骰子表达式</span>
            <input
              type="text"
              value={spec}
              maxLength={20}
              list="roll-presets"
              onChange={(event) => {
                setSpec(event.target.value);
                setError(null);
              }}
            />
            <datalist id="roll-presets">
              {["1d100", "1d20", "1d10", "1d8", "1d6", "2d6", "3d6"].map(
                (preset) => (
                  <option key={preset} value={preset} />
                ),
              )}
            </datalist>
            <span className="panel-action-note">
              普通掷骰只出结果：不自动发线索、不扣 SAN、不触发检定成功分支，
              也不能用来绕过重复检定限制。
            </span>
          </label>
          {!identity?.investigatorId && (
            <p className="panel-action-error" role="alert">
              尚未确定行动调查员，暂时无法掷骰。
            </p>
          )}
          {error && (
            <p className="panel-action-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer className="panel-action-footer">
          <button
            type="button"
            className="btn-ghost panel-action-cancel"
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="button"
            id="roll-confirm"
            data-testid="roll-confirm"
            className="btn-primary panel-action-confirm"
            disabled={invalid !== null || !identity?.investigatorId}
            title={invalid ?? "提交普通掷骰请求"}
            onClick={submit}
          >
            掷骰
          </button>
        </footer>
      </div>
    </div>
  );
}

/** 测试导出：待响应检定的选择器（保持与组件一致）。 */
export function checksFor(
  state: Parameters<typeof pendingChecksFor>[0],
  investigatorId: string,
) {
  return pendingChecksFor(state, investigatorId);
}
