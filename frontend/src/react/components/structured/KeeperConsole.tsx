/**
 * KeeperConsole.tsx — 最小人类主持台。
 *
 * 职责：把主持操作变成明确的 `command_request`，并显示服务端返回的状态。
 * 不做任何“看起来成功”的本地推断：命令提交后只是 `queued`，结果以服务端
 * 事件为准；没有对应能力的操作明确禁用，而不是画一个假的可用入口。
 *
 * 授权：keeper 身份来自服务端投影（`session_snapshot.keeper`）。房主
 * （owner）不等于 keeper；只有服务端把当前用户标为 keeper 才显示控制台。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { parseRulingValue } from "../../../protocol/rulings";
import { KeeperEndingCatalogue } from "./KeeperEndingCatalogue";
import { KeeperProgressCatalogue } from "./KeeperProgressCatalogue";
import { KeeperSaveActions } from "./KeeperSaveActions";
import { GameClockReadout } from "./GameClockReadout";
import { CONDITION_LABELS } from "../../../protocol/conditions";
import { TIME_ACTIVITY_LABELS } from "../../../protocol/time-activity";
import {
  ConditionPreviousRecord,
  ConditionRecordReference,
  conditionRecordProblem,
} from "./ConditionRecordReference";
import { focusableControls, trapDialogTab } from "../dialogFocus";

import {
  keeperCommandBlockReason,
  resubmitWithFreshRevision,
  sendKeeperCommand,
} from "../../../structured-transport";
import {
  KEEPER_COMMANDS,
  buildKeeperPayload,
  candidatesFor,
  emptyKeeperValues,
  findKeeperCommand,
  validateKeeperFields,
  type CommandField,
  type FieldValues,
  type KeeperCandidates,
  type KeeperCommandSpec,
} from "../../../protocol/keeper-commands";
import { useAppStore } from "../../../state/app-store";
import {
  structuredUnavailableReason,
  type StructuredAction,
} from "../../../protocol/structured";
import { sendMemoryQuery } from "../../../structured-transport";
import {
  activeRequests,
  openInteractions,
  useStructuredStore,
  type KeeperInvestigator,
} from "../../../state/structured-store";
import { useOnlineStore } from "../../../state/online-store";
import { KeeperLibrary } from "./KeeperLibrary";

/** keeper 判据：服务端投影为准，本地单机允许 keeperUserId 为空。 */
/** 旧世界名册缺失时 public_investigator_roster 的兜底 id（不是真实调查员）。 */
const LEGACY_PLACEHOLDER_ID = "legacy-pc";

// Labels are presentation only. Wire values still follow the frozen schema.
const CHOICE_LABELS: Record<string, string> = {
  ...CONDITION_LABELS,
  add: "添加",
  remove: "移除",
  melee: "近战",
  firearm: "射击",
  threat: "威胁",
  queued: "待主持处理",
  processing: "处理中",
  failed: "处理失败",
  take: "接管主持",
  release: "归还给 AI",
  retry: "重试暂停请求",
  keeper: "仅主持",
  npc: "人物",
  investigator: "调查员",
  system: "系统通知",
  public: "所有人",
  investigators: "指定调查员",
  describe: "说明内容",
  image: "展示图片",
  original: "展示原件",
  regular: "普通",
  hard: "困难",
  extreme: "极难",
  hp: "生命值（HP）",
  san: "理智值（SAN）",
  max_hp: "生命上限",
  max_san: "理智上限",
  enter: "进入场景",
  leave: "离开场景",
  completed: "已处理",
  declined: "拒绝执行",
  cancelled: "取消",
  paused: "暂停",
  awaiting_player: "等待玩家回应",
  open: "开启",
  continue: "继续",
  close: "关闭",
  replace: "替换",
  move: "移动",
  freeform: "自由行动",
  present_clue: "出示线索",
  use_item: "使用道具",
  other: "其他",
  success: "已执行 · 成功",
  failure: "已执行 · 失败",
  not_executed: "尚未执行",
  experienced: "亲历",
  told: "他人告知",
  rumor: "传闻",
  belief: "角色看法",
  approved: "批准",
  rejected: "驳回",
  edited: "修改后批准",
  module: "模组设定",
  ruling: "主持裁定",
};

export function actionTitle(
  action: StructuredAction | undefined,
  fallback: string,
  candidates: Partial<KeeperCandidates> = {},
): string {
  const name = (id: string, entries: { id: string; name: string }[] = []) =>
    entries.find((entry) => entry.id === id)?.name.trim() || id;
  if (!action || action.kind === "freeform") return fallback;
  switch (action.kind) {
    case "move":
      return `申请前往：${name(action.destination_scene_id, candidates.scenes)}`;
    case "present_clue":
      return `申请出示：${name(action.clue_id, candidates.clues)}`;
    case "use_item":
      return `申请使用：${name(action.item_id, candidates.items)} ×${action.quantity}`;
    case "combat":
      return `申报战斗动作：${CHOICE_LABELS[action.action_type] || action.action_type}`;
  }
}

export function actionBody(
  action: StructuredAction | undefined,
  candidates: Partial<KeeperCandidates> = {},
): string {
  if (!action) return "服务端未提供完整请求正文。";
  const named = (id: string, entries: { id: string; name: string }[] = []) => {
    const name = entries.find((entry) => entry.id === id)?.name.trim();
    return name && name !== id ? `${name}（${id}）` : id;
  };
  const target =
    "target" in action && action.target
      ? "id" in action.target
        ? named(
            action.target.id,
            action.target.kind === "npc"
              ? candidates.npcs
              : action.target.kind === "investigator"
                ? candidates.investigators
                : candidates.objects,
          )
        : action.target.text
      : "未指定";
  switch (action.kind) {
    case "freeform":
      return action.text;
    case "move":
      return `申请前往：${named(action.destination_scene_id, candidates.scenes)}`;
    case "present_clue":
      return `线索：${named(action.clue_id, candidates.clues)}\n方式：${CHOICE_LABELS[action.presentation]}${action.physical_item_id ? `\n出示实物：${named(action.physical_item_id, candidates.items)}` : ""}\n目标：${target}\n${action.question || ""}`;
    case "use_item":
      return `物品：${named(action.item_id, candidates.items)}\n本次申请数量：${action.quantity}\n用法：${action.operation === "custom" ? "即兴用法（custom）" : action.operation}\n目标：${target}\n${action.approach || ""}`;
    case "combat":
      return `动作：${CHOICE_LABELS[action.action_type] || action.action_type}\n目标：${action.target_id ? named(action.target_id, [...(candidates.investigators || []), ...(candidates.npcs || [])]) : "未指定"}\n遭遇：${action.encounter_id}\n${action.approach || ""}`;
  }
}

export function keeperAuthorized(
  keeperUserId: string | null,
  keeperMode: string | null,
  currentUserId: string | null,
  localMode = false,
): boolean {
  if (!keeperMode) return false;
  if (currentUserId) {
    // 有账号身份：必须与服务端当前的 keeper 控制者一致（房主 ≠ keeper）。
    // 尚无成员投影时，只信服务端的当前 keeper；完整成员投影在组件内单独复核。
    return keeperUserId === currentUserId;
  }
  // 本地单机没有账号身份：服务端给出的 keeper_mode 表示本机操作者就是主持
  // （单窗口本地不承诺跨设备隐私，见规格 §4）。
  return localMode;
}

export function KeeperConsole() {
  const capabilities = useStructuredStore((state) => state.capabilities);
  const protocolNotice = useStructuredStore((state) => state.protocolNotice);
  const identity = useStructuredStore((state) => state.identity);
  const targets = useStructuredStore((state) => state.targets);
  const destinations = useStructuredStore((state) => state.destinations);
  const clues = useStructuredStore((state) => state.clues);
  const keeperProgress = useStructuredStore((state) => state.keeperProgress);
  const currentSceneId = useStructuredStore((state) => state.currentSceneId);
  const items = useStructuredStore((state) => state.items);
  const keeperMaterial = useStructuredStore((state) => state.keeperMaterial);
  const keeperRulings = useStructuredStore((state) => state.keeperRulings);
  const keeperAssets = useStructuredStore((state) => state.keeperAssets);
  const combat = useStructuredStore((state) => state.combat);
  const keeperInvestigators = useStructuredStore(
    (state) => state.keeperInvestigators,
  );
  const requestsMap = useStructuredStore((state) => state.requests);
  const requestOrder = useStructuredStore((state) => state.requestOrder);
  const interactionMap = useStructuredStore((state) => state.interactions);
  const interactionOrder = useStructuredStore(
    (state) => state.interactionOrder,
  );
  const currentUserId = useOnlineStore((state) => state.user?.id ?? null);

  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<Element | null>(null);
  const [activeKind, setActiveKind] = useState<string>("publish_message");
  const [values, setValues] = useState<FieldValues>(() =>
    emptyKeeperValues(findKeeperCommand("publish_message")!),
  );
  const [drafts, setDrafts] = useState<Record<string, FieldValues>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [feedback, setFeedback] = useState<string>("");
  const [submittedId, setSubmittedId] = useState<string | null>(null);
  const [preparedCombatRequest, setPreparedCombatRequest] = useState<
    string | undefined
  >();

  const appMode = useAppStore((state) => state.mode);
  const connection = useAppStore((state) => state.connection);
  const roomKeeper = useOnlineStore((state) => {
    const uid = state.user?.id;
    return (
      uid != null &&
      state.members.some(
        (member) => member.user_id === uid && member.can_keeper === true,
      )
    );
  });
  // 房主与主持授权独立；成员投影中的 can_keeper 是云端入口判据。
  const authorized =
    appMode === "online"
      ? roomKeeper
      : keeperAuthorized(
          identity.keeperUserId,
          identity.keeperMode,
          currentUserId,
          appMode === "local",
        );
  const blocked =
    structuredUnavailableReason(capabilities, protocolNotice) ??
    (connection === "connected"
      ? null
      : connection === "connecting"
        ? "正在连接并同步权威状态，请求未提交。"
        : "连接已断开，请求未提交。");
  const commandBlocked = keeperCommandBlockReason(activeKind);

  const roomInvestigators = useOnlineStore((state) => state.roomInvestigators);
  const roomMembers = useOnlineStore((state) => state.members);
  // 候选列表要在 candidates 之前算：主持台里 request_id / thread_id 都是稳定 ID，
  // 只能从服务端投影里挑，不能手抄。
  const pendingRequests = useMemo(
    () => activeRequests({ requests: requestsMap, requestOrder }),
    [requestsMap, requestOrder],
  );
  const openThreads = useMemo(
    () => openInteractions({ interactions: interactionMap, interactionOrder }),
    [interactionMap, interactionOrder],
  );
  const candidates: KeeperCandidates = useMemo(
    () => ({
      combatants: combat?.active
        ? combat.participants?.map((p) => ({ id: p.id, name: p.name }))
        : undefined,
      flags:
        keeperRulings?.flags.map((flag) => ({
          ...flag,
          name: `${flag.id} · 当前：${flag.value === null ? "未记录" : String(flag.value)}`,
        })) || [],
      // 房间调查员名单来自房间镜像与成员信息：keeper 需要它指定线索接收者、
      // 检定对象与 HP/SAN 目标。房间快照的 targets 可能不含调查员，不能只靠它。
      investigators: [
        ...keeperInvestigators.map((sheet) => ({
          id: sheet.investigatorId,
          name: sheet.name,
        })),
        ...roomInvestigators.map((entry) => ({
          id: String(entry.investigator_id ?? ""),
          name: String(entry.name ?? entry.investigator_id ?? ""),
        })),
        ...roomMembers.flatMap((member) => {
          // 结构化层的调查员标识是 character_key（claim 行 id 是另一套标识，
          // 用它发命令会 object_not_found）。
          const investigator = member.investigator;
          const key = String(investigator?.character_key ?? "");
          if (!key) return [];
          return [
            { id: key, name: String(investigator?.name ?? member.username) },
          ];
        }),
        ...targets
          .filter((target) => target.kind === "investigator")
          .map((target) => ({ id: target.id, name: target.name })),
      ]
        .filter((entry) => entry.id)
        .filter(
          (entry, index, all) =>
            all.findIndex((other) => other.id === entry.id) === index,
        )
        // 房间镜像可能是在开局物化名册之前取的，里面会带着旧世界的占位
        // 调查员 id（public_investigator_roster 的兜底值）。有真实候选时把它
        // 去掉，避免主持选中一个服务端必然会拒的 id。
        .filter(
          (entry, _index, all) =>
            entry.id !== LEGACY_PLACEHOLDER_ID || all.length === 1,
        ),
      npcs: [
        ...targets
          .filter((target) => target.kind === "npc")
          .map((target) => ({ id: target.id, name: target.name })),
        ...keeperMaterial
          .filter((entry) => entry.kind === "npc" && entry.id)
          .map((entry) => ({ id: entry.id!, name: entry.title })),
      ].filter(
        (entry, index, all) =>
          all.findIndex((other) => other.id === entry.id) === index,
      ),
      scenes: destinations.map((scene) => ({ id: scene.id, name: scene.name })),
      objects: [
        ...targets
          .filter((target) => target.kind === "scene_object")
          .map((target) => ({ id: target.id, name: target.name })),
        ...(keeperProgress?.clues || [])
          .filter(
            (c) =>
              c.rules.some((r) => r.requires_success) &&
              (!c.related_scenes.length ||
                c.related_scenes.includes(currentSceneId)),
          )
          .map((c) => ({
            id: c.id,
            name: `发现检定对象 · ${c.granted_item || c.text.slice(0, 40) || c.id}`,
          })),
      ].filter(
        (entry, index, all) =>
          all.findIndex((e) => e.id === entry.id) === index,
      ),
      clues: [...(keeperProgress?.clues || []), ...clues]
        .filter(
          (c, i, all) => all.findIndex((other) => other.id === c.id) === i,
        )
        .map((clue) => ({
          id: clue.id,
          name: clue.text.slice(0, 40) || clue.id,
        })),
      items: (keeperInvestigators.length
        ? keeperInvestigators.flatMap((sheet) => sheet.inventory)
        : items
      )
        .filter(
          (item, index, all) =>
            all.findIndex((other) => other.id === item.id) === index,
        )
        .map((item) => ({
          id: item.id,
          name: `${item.label} ×${item.quantity}`,
        })),
      assets: keeperAssets.map((entry) => ({
        id: entry.id,
        name: entry.label,
      })),
      // 玩家请求与「当前交互」线程：主持收尾/关线程都要用稳定 ID，
      // 不能让主持手抄卡片上根本不显示的值（猜错 ID 服务端必拒）。
      requests: pendingRequests.map((request) => ({
        id: request.requestId,
        name: `${request.kind || "请求"} · ${request.detail || request.requestId}`.slice(
          0,
          80,
        ),
      })),
      threads: openThreads.map((thread) => ({
        id: thread.threadId,
        name: `${thread.pendingAction.note || thread.status} · ${thread.threadId}`.slice(
          0,
          80,
        ),
      })),
    }),
    [
      targets,
      combat,
      destinations,
      clues,
      keeperProgress,
      currentSceneId,
      items,
      keeperAssets,
      keeperMaterial,
      keeperRulings,
      keeperInvestigators,
      roomInvestigators,
      roomMembers,
      pendingRequests,
      openThreads,
    ],
  );

  const requestCatalog = (investigatorId: string | null | undefined) => ({
    ...candidates,
    // 不借用汇总候选里其他角色的库存数量。
    items: (
      keeperInvestigators.find(
        (sheet) => sheet.investigatorId === investigatorId,
      )?.inventory ?? items
    ).map((item) => ({ id: item.id, name: item.label })),
  });

  const spec = findKeeperCommand(activeKind);

  // Switching worlds must not carry private notes or object IDs into another game.
  useEffect(() => {
    setOpen(false);
    setActiveKind("publish_message");
    setValues(emptyKeeperValues(findKeeperCommand("publish_message")!));
    setDrafts({});
    setErrors([]);
    setFeedback("");
    setSubmittedId(null);
    setPreparedCombatRequest(undefined);
  }, [identity.worldId, currentUserId, appMode]);

  useEffect(() => {
    if (authorized) return;
    setOpen(false);
    setErrors([]);
    setValues(emptyKeeperValues(findKeeperCommand("publish_message")!));
    setDrafts({});
    setFeedback("");
    setSubmittedId(null);
    setPreparedCombatRequest(undefined);
  }, [authorized]);

  const submitted = submittedId ? requestsMap[submittedId] : null;
  const submissionPending =
    submitted != null &&
    !submitted.errorCode &&
    (submitted.status === "queued" || submitted.status === "processing");
  const resultFeedback = submitted?.errorMessage
    ? `未完成：${submitted.errorMessage}。草稿已保留。`
    : submitted?.status === "completed"
      ? `服务端已确认提交${submitted.detail ? `：${submitted.detail}` : "。"}行动是否成功请以实际结算事件为准。`
      : submitted?.status === "declined" || submitted?.status === "failed"
        ? `未完成：${submitted.detail || "服务端未能执行本次命令"}。草稿已保留。`
        : submitted?.status === "paused" ||
            submitted?.status === "cancelled" ||
            submitted?.status === "awaiting_player"
          ? `${CHOICE_LABELS[submitted.status]}：${submitted.detail || "请查看待处理行动"}。草稿已保留。`
          : submitted?.awaitingAck
            ? "尚未收到服务端确认，正在查询原请求状态；不要重复执行同一操作。"
            : submitted?.serverReceived
              ? "服务端已收件，等待本次命令结算；不要重复提交。"
              : feedback;

  const submit = () => {
    if (!spec || submissionPending) return;
    const problems = validateKeeperFields(spec, values);
    if (spec.kind === "record_condition") {
      const issue = conditionRecordProblem(
        keeperInvestigators.find(
          (sheet) => sheet.investigatorId === values.investigator_id,
        ),
        values,
      );
      if (issue) problems.push(issue);
    }
    setErrors(problems);
    if (problems.length) return;
    if (commandBlocked) {
      setErrors([commandBlocked]);
      return;
    }
    const payload = buildKeeperPayload(spec, values);
    const result = sendKeeperCommand(
      spec.kind,
      payload,
      undefined,
      spec.kind === "combat_action" ? preparedCombatRequest : undefined,
    );
    if (!result.ok) {
      setErrors([result.reason]);
      setFeedback("");
      return;
    }
    setFeedback(
      `正在提交「${spec.label}」，等待服务端确认；发出请求不代表执行成功。`,
    );
    setSubmittedId(result.requestId);
  };

  const prepareCommand = (
    kind: string,
    fields: FieldValues,
    causeId?: string,
  ) => {
    const next = findKeeperCommand(kind);
    if (!next) return;
    setDrafts((old) => ({ ...old, [activeKind]: { ...values } }));
    setActiveKind(kind);
    setPreparedCombatRequest(causeId);
    setValues({ ...emptyKeeperValues(next), ...fields });
    setErrors([]);
    setFeedback("");
    setSubmittedId(null);
    window.requestAnimationFrame(() => {
      const field = dialogRef.current?.querySelector<HTMLElement>(
        ".keeper-workspace-editor .keeper-command-form select, .keeper-workspace-editor .keeper-command-form input, .keeper-workspace-editor .keeper-command-form textarea",
      );
      field?.scrollIntoView({ block: "center" });
      field?.focus({ preventScroll: true });
    });
  };

  // 键盘可用：打开时聚焦表单首个控件，Escape 关闭，关闭后焦点回到触发按钮。
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement;
    const controls = dialogRef.current
      ? focusableControls(dialogRef.current)
      : [];
    const first =
      controls.find((node) => node.matches("textarea")) ?? controls[0];
    first?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      const dialog = dialogRef.current;
      if (
        !dialog ||
        event.isComposing ||
        !dialog.contains(document.activeElement)
      )
        return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
      } else if (event.key === "Tab") {
        trapDialogTab(event, dialog);
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      const restore = restoreFocusRef.current;
      if (restore instanceof HTMLElement && restore.isConnected)
        restore.focus();
    };
  }, [open]);

  if (!authorized || (!capabilities.keeperConsole && !protocolNotice))
    return null;

  return (
    <>
      <button
        type="button"
        className="btn-ghost keeper-console-toggle"
        id="btn-keeper-console"
        data-testid="btn-keeper-console"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        主持台
      </button>
      {open && (
        <div id="keeper-console-overlay" className="structured-overlay-inline">
          <div
            id="keeper-console"
            role="dialog"
            aria-modal="true"
            aria-labelledby="keeper-console-title"
            ref={dialogRef}
          >
            <header className="panel-action-header">
              <div>
                <span className="keeper-workspace-eyebrow">
                  {authorized ? "仅主持可见" : "需要主持授权"}
                </span>
                <h3 id="keeper-console-title">主持工作台</h3>
              </div>
              <button
                type="button"
                className="btn-ghost panel-action-close"
                aria-label="关闭主持台"
                onClick={() => setOpen(false)}
              >
                ✕
              </button>
            </header>

            <div className="panel-action-body keeper-console-body">
              {protocolNotice && (
                <p className="panel-action-error" role="alert">
                  {protocolNotice}
                </p>
              )}
              {blocked && !protocolNotice && (
                <p className="online-notice" role="status">
                  {blocked}
                </p>
              )}
              {!authorized && (
                <p className="online-notice" data-testid="keeper-unauthorized">
                  你不是本场主持。房主身份不等于主持权限，需要服务端把 keeper
                  身份授予你才会显示主持操作。
                </p>
              )}

              {authorized && (
                <KeeperLibrary
                  investigators={candidates.investigators}
                  blocked={blocked}
                  onPrepare={prepareCommand}
                />
              )}

              <aside
                className="keeper-workspace-reference"
                aria-label="本场参考资料"
              >
                <section aria-label="待处理行动">
                  <h4 className="keeper-section-title">待处理行动</h4>
                  {pendingRequests.length === 0 ? (
                    <p className="clue-empty">暂无待处理请求</p>
                  ) : (
                    <ul className="keeper-pending-list">
                      {pendingRequests.map((request) => (
                        <li
                          key={request.requestId}
                          data-testid="keeper-pending-request"
                        >
                          {request.investigatorId && (
                            <p className="keeper-note">
                              来自{" "}
                              {candidates.investigators.find(
                                (entry) => entry.id === request.investigatorId,
                              )?.name || request.investigatorId}
                            </p>
                          )}
                          <span
                            className="keeper-pending-label"
                            title={request.label}
                          >
                            {actionTitle(
                              request.keeperAction,
                              request.label,
                              requestCatalog(request.investigatorId),
                            )}
                          </span>
                          <code className="keeper-pending-id">
                            {request.requestId}
                          </code>
                          <span
                            className={`structured-badge structured-badge--${request.status}`}
                          >
                            {CHOICE_LABELS[request.status] ?? request.status}
                          </span>
                          <details>
                            <summary>查看完整请求</summary>
                            <p className="keeper-pending-body">
                              {actionBody(
                                request.keeperAction,
                                requestCatalog(request.investigatorId),
                              )}
                            </p>
                          </details>
                          <p className="keeper-note">
                            请求尚未收尾；下方只准备表单，不自动批准或结算。
                          </p>
                          <div className="keeper-pending-actions">
                            {request.investigatorId &&
                              request.keeperAction?.kind === "combat" && (
                                <button
                                  type="button"
                                  className="btn-ghost"
                                  disabled={
                                    blocked !== null ||
                                    !capabilities.commands.includes(
                                      "combat_action",
                                    )
                                  }
                                  onClick={() => {
                                    const action = request.keeperAction;
                                    if (action?.kind !== "combat") return;
                                    prepareCommand(
                                      "combat_action",
                                      {
                                        actor_id: request.investigatorId!,
                                        action_type: action.action_type,
                                        target_id: action.target_id || "",
                                        description: action.approach || "",
                                        weapon_item_id:
                                          action.weapon_item_id || "",
                                      },
                                      request.requestId,
                                    );
                                  }}
                                >
                                  准备战斗动作
                                </button>
                              )}
                            {request.investigatorId &&
                              request.keeperAction?.kind === "use_item" && (
                                <button
                                  type="button"
                                  className="btn-ghost"
                                  disabled={
                                    blocked !== null ||
                                    !capabilities.commands.includes("use_item")
                                  }
                                  onClick={() => {
                                    const action = request.keeperAction;
                                    if (action?.kind !== "use_item") return;
                                    prepareCommand("use_item", {
                                      investigator_id: request.investigatorId!,
                                      item_id: action.item_id,
                                      quantity: action.quantity,
                                      operation: action.operation,
                                      approach: action.approach || "",
                                      consume: false,
                                      ...(action.target
                                        ? {
                                            target_kind: action.target.kind,
                                            target_id:
                                              "id" in action.target
                                                ? action.target.id
                                                : action.target.text,
                                          }
                                        : {}),
                                    });
                                  }}
                                >
                                  准备使用
                                </button>
                              )}
                            <button
                              type="button"
                              className="btn-ghost"
                              disabled={
                                blocked !== null ||
                                !capabilities.commands.includes(
                                  "publish_message",
                                )
                              }
                              onClick={() =>
                                prepareCommand(
                                  "publish_message",
                                  request.investigatorId
                                    ? {
                                        audience_kind: "investigators",
                                        audience_investigator_ids:
                                          request.investigatorId,
                                      }
                                    : {},
                                )
                              }
                            >
                              准备回应
                            </button>
                            <button
                              type="button"
                              className="btn-ghost"
                              disabled={
                                blocked !== null ||
                                !capabilities.commands.includes(
                                  "resolve_intent",
                                )
                              }
                              onClick={() =>
                                prepareCommand("resolve_intent", {
                                  request_id: request.requestId,
                                  outcome: "not_executed",
                                })
                              }
                            >
                              准备裁定
                            </button>
                            {request.investigatorId && (
                              <button
                                type="button"
                                className="btn-ghost"
                                disabled={
                                  blocked !== null ||
                                  !capabilities.commands.includes(
                                    "request_check",
                                  )
                                }
                                onClick={() =>
                                  prepareCommand("request_check", {
                                    related_request_id: request.requestId,
                                    investigator_id: request.investigatorId!,
                                  })
                                }
                              >
                                准备检定
                              </button>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section aria-label="授权资料">
                  <h4 className="keeper-section-title">授权模组资料</h4>
                  <p className="keeper-note">
                    本场已授权给你的模组资料。分发线索时请选择接收调查员；未授权的玩家不会看到主持资料。
                  </p>
                  <dl className="structured-facts">
                    <div>
                      <dt>线索登记表</dt>
                      <dd>{clues.length} 条</dd>
                    </div>
                    <div>
                      <dt>物品</dt>
                      <dd>{items.length} 项</dd>
                    </div>
                    <div>
                      <dt>可交互目标</dt>
                      <dd>{targets.length} 个</dd>
                    </div>
                    <div>
                      <dt>公开目的地</dt>
                      <dd>{destinations.length} 处</dd>
                    </div>
                  </dl>
                  {authorized && keeperMaterial.length > 0 ? (
                    <details className="keeper-clue-details">
                      <summary>
                        主持参考（{keeperMaterial.length} 条）·
                        上方资料库可分类查阅
                      </summary>
                      <ul
                        className="keeper-material-list"
                        data-testid="keeper-material"
                      >
                        {keeperMaterial.map((entry) => (
                          <li key={`${entry.title}-${entry.text.slice(0, 12)}`}>
                            <strong>{entry.title}</strong>
                            <p>{entry.text}</p>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ) : (
                    <p
                      className="keeper-note"
                      data-testid="keeper-material-missing"
                    >
                      本模组暂未提供额外的主持资料。可先使用线索登记表和下方主持操作。
                    </p>
                  )}
                  {clues.length > 0 && (
                    <details className="keeper-clue-details">
                      <summary>完整线索登记表（{clues.length}）</summary>
                      <ul className="keeper-clue-list">
                        {clues.map((clue) => (
                          <li key={clue.id}>
                            <code>{clue.id}</code> {clue.text.slice(0, 60)}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </section>

                {capabilities.memoryQuery && authorized && (
                  <MemoryQueryPanel candidates={candidates} blocked={blocked} />
                )}
                <KeeperSaveActions />
              </aside>
              <div className="keeper-workspace-editor">
                {keeperProgress && (
                  <KeeperProgressCatalogue
                    state={keeperProgress}
                    sceneId={currentSceneId}
                    blocked={blocked}
                    onPrepare={prepareCommand}
                  />
                )}
                {keeperRulings && (
                  <details
                    className="keeper-ruling-audit"
                    data-testid="keeper-ruling-audit"
                  >
                    <summary>剧情裁定与结局资格</summary>
                    <p className="structured-card-note">
                      仅主持可见。前置条件满足不等于立即结束游戏，结局仍须显式结算。
                    </p>
                    <KeeperEndingCatalogue
                      state={keeperRulings}
                      blocked={
                        keeperCommandBlockReason("end_game") ||
                        (combat?.active ? "请先结算或明确结束当前战斗。" : null)
                      }
                      onPrepare={(id) =>
                        prepareCommand("end_game", { ending_id: id })
                      }
                    />
                    <ol>
                      {keeperRulings.recent
                        .slice(-5)
                        .reverse()
                        .map((ruling, i) => (
                          <li key={`${ruling.revision}-${i}`}>
                            <p>
                              {ruling.flag_id}：{String(ruling.before)} →{" "}
                              {String(ruling.after)} · 版本 {ruling.revision}
                            </p>
                            <p>依据：{ruling.basis}</p>
                            <p>裁定者：{ruling.user_id}</p>
                          </li>
                        ))}
                    </ol>
                  </details>
                )}
                <section aria-label="主持操作">
                  <h4 className="keeper-section-title">主持操作</h4>
                  <div className="keeper-command-groups">
                    {Array.from(
                      new Set(KEEPER_COMMANDS.map((command) => command.group)),
                    ).map((group) => (
                      <fieldset className="keeper-operation-group" key={group}>
                        <legend>{group}</legend>
                        <div className="keeper-operation-buttons">
                          {KEEPER_COMMANDS.filter(
                            (command) => command.group === group,
                          ).map((command) => {
                            const disabled =
                              blocked !== null ||
                              !authorized ||
                              !capabilities.commands.includes(command.kind) ||
                              (command.kind === "move_party" &&
                                !capabilities.moveAction) ||
                              (command.kind === "request_check" &&
                                !capabilities.checkRequest);
                            return (
                              <button
                                key={command.kind}
                                type="button"
                                className={
                                  activeKind === command.kind
                                    ? "btn-ghost keeper-command is-active"
                                    : "btn-ghost keeper-command"
                                }
                                data-testid={`keeper-cmd-${command.kind}`}
                                aria-pressed={activeKind === command.kind}
                                disabled={disabled}
                                title={
                                  !authorized
                                    ? "需要主持权限"
                                    : (blocked ??
                                      (disabled
                                        ? "服务端未声明该能力"
                                        : (command.help ?? command.label)))
                                }
                                onClick={() => {
                                  if (activeKind === command.kind) return;
                                  setDrafts((current) => ({
                                    ...current,
                                    [activeKind]: values,
                                  }));
                                  setActiveKind(command.kind);
                                  setPreparedCombatRequest(undefined);
                                  setValues(
                                    drafts[command.kind] ??
                                      emptyKeeperValues(command),
                                  );
                                  setErrors([]);
                                  setFeedback("");
                                  setSubmittedId(null);
                                }}
                              >
                                {command.label}
                              </button>
                            );
                          })}
                        </div>
                      </fieldset>
                    ))}
                  </div>
                </section>

                {spec && authorized && blocked === null && (
                  <section
                    aria-label="命令表单"
                    className="keeper-command-form"
                    data-command={spec.kind}
                  >
                    <h4 className="keeper-section-title">{spec.label}</h4>
                    {spec.help && <p className="keeper-note">{spec.help}</p>}
                    {spec.kind === "advance_time" && (
                      <>
                        <GameClockReadout variant="reference" />
                        <p className="keeper-note">
                          只有提交并结算后才会改变游戏时间。
                        </p>
                      </>
                    )}
                    {spec.kind === "record_condition" && (
                      <ConditionRecordReference
                        sheet={keeperInvestigators.find(
                          (sheet) =>
                            sheet.investigatorId === values.investigator_id,
                        )}
                      />
                    )}
                    {commandBlocked && (
                      <p role="status" className="keeper-note">
                        {commandBlocked}
                      </p>
                    )}
                    {spec.fields.map((field) => (
                      <div
                        className="keeper-field-slot"
                        data-field={field.name}
                        key={`${field.name}-slot`}
                      >
                        <KeeperField
                          field={field}
                          spec={spec}
                          values={values}
                          candidates={
                            spec.kind === "combat_action"
                              ? {
                                  ...candidates,
                                  items: (
                                    keeperInvestigators.find(
                                      (sheet) =>
                                        sheet.investigatorId ===
                                        values.actor_id,
                                    )?.inventory ||
                                    (identity.investigatorId === values.actor_id
                                      ? items
                                      : [])
                                  ).map((item) => ({
                                    id: item.id,
                                    name: `${item.label} · ×${item.quantity} · ${item.id.slice(-8)}`,
                                  })),
                                }
                              : candidates
                          }
                          keeperInvestigators={keeperInvestigators}
                          disabled={
                            commandBlocked !== null ||
                            !authorized ||
                            (field.name === "weapon_item_id" &&
                              !capabilities.combatWeaponItemId)
                          }
                          onChange={(name, value) =>
                            setValues((current) => {
                              if (
                                spec.kind === "record_condition" &&
                                (name === "investigator_id" ||
                                  name === "condition")
                              ) {
                                const next = { ...current, [name]: value };
                                const sheet = keeperInvestigators.find(
                                  (entry) =>
                                    entry.investigatorId ===
                                    next.investigator_id,
                                );
                                return {
                                  ...next,
                                  expected_present: sheet
                                    ? sheet.conditions.includes(
                                        String(next.condition),
                                      )
                                    : "",
                                };
                              }
                              if (
                                spec.kind === "combat_action" &&
                                name === "actor_id"
                              )
                                return {
                                  ...current,
                                  actor_id: value,
                                  weapon_item_id: "",
                                };
                              if (
                                spec.kind === "record_ruling" &&
                                name === "flag_id"
                              ) {
                                const flag = candidates.flags?.find(
                                  (f) => f.id === value,
                                );
                                return {
                                  ...current,
                                  flag_id: value,
                                  expected_before: flag
                                    ? JSON.stringify(flag.value)
                                    : "",
                                  value: flag
                                    ? JSON.stringify(
                                        flag.value ??
                                          (flag.type === "boolean"
                                            ? false
                                            : flag.type === "integer"
                                              ? 0
                                              : ""),
                                      )
                                    : "",
                                };
                              }
                              return { ...current, [name]: value };
                            })
                          }
                        />
                      </div>
                    ))}
                    {errors.length > 0 && (
                      <ul className="panel-action-error" role="alert">
                        {errors.map((error) => (
                          <li key={error}>{error}</li>
                        ))}
                      </ul>
                    )}
                    {resultFeedback && (
                      <p className="keeper-feedback" role="status">
                        {resultFeedback}
                      </p>
                    )}
                    {!resultFeedback.includes("不代表执行成功") && (
                      <p className="keeper-note">
                        请求已接收不代表执行成功；请以服务端结算结果为准。
                      </p>
                    )}
                    <div className="structured-card-actions">
                      {submitted?.errorCode === "revision_conflict" && (
                        <button
                          type="button"
                          className="btn-ghost structured-btn"
                          disabled={commandBlocked !== null || !authorized}
                          onClick={() => {
                            const result = resubmitWithFreshRevision(
                              submitted.requestId,
                            );
                            if (!result) {
                              setErrors([
                                "尚未收到更新后的世界状态，请等待同步后再试。",
                              ]);
                            } else if (!result.ok) {
                              setErrors([result.reason]);
                            } else {
                              setErrors([]);
                              setSubmittedId(result.requestId);
                              setFeedback(
                                "已按最新世界版本重新提交，等待服务端确认。",
                              );
                            }
                          }}
                        >
                          用最新版本重新提交
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-primary structured-btn"
                        data-testid="keeper-submit"
                        disabled={
                          commandBlocked !== null ||
                          !authorized ||
                          submissionPending
                        }
                        title={commandBlocked ?? "提交主持命令"}
                        onClick={submit}
                      >
                        {submissionPending
                          ? submitted?.serverReceived
                            ? "等待命令结算……"
                            : "等待收件确认……"
                          : spec.kind === "publish_message"
                            ? "发布叙事"
                            : spec.kind === "record_condition"
                              ? "记录变更"
                              : "提交命令"}
                      </button>
                    </div>
                  </section>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function KeeperField({
  field,
  spec,
  values,
  candidates,
  keeperInvestigators,
  disabled,
  onChange,
}: {
  field: CommandField;
  spec: KeeperCommandSpec;
  values: FieldValues;
  candidates: KeeperCandidates;
  keeperInvestigators: readonly KeeperInvestigator[];
  disabled: boolean;
  onChange: (name: string, value: string | number | boolean) => void;
}) {
  if (spec.kind === "record_condition" && field.name === "expected_present") {
    return (
      <ConditionPreviousRecord
        sheet={keeperInvestigators.find(
          (sheet) => sheet.investigatorId === values.investigator_id,
        )}
        condition={String(values.condition || "")}
        previous={values.expected_present}
        disabled={disabled}
        onRefresh={(present) => onChange(field.name, present)}
      />
    );
  }
  if (field.kind === "primitive") {
    const flag = candidates.flags?.find((f) => f.id === values.flag_id);
    const value = parseRulingValue(values[field.name]);
    if (!flag)
      return <p className="structured-card-note">先选择已有剧情条件。</p>;
    if (field.name === "expected_before")
      return (
        <div className="keeper-ruling-previous">
          <span>
            {field.label}：
            <output>
              {value === null
                ? "未记录"
                : typeof value === "boolean"
                  ? value
                    ? "成立"
                    : "不成立"
                  : String(value ?? "")}
            </output>
          </span>
          <button
            type="button"
            className="btn-ghost structured-btn"
            disabled={disabled}
            onClick={() => onChange(field.name, JSON.stringify(flag.value))}
          >
            读取当前值
          </button>
        </div>
      );
    if (flag.type === "boolean")
      return (
        <label className="panel-action-field keeper-field-inline keeper-ruling-toggle">
          <span>{field.label}</span>
          <input
            type="checkbox"
            checked={value === true}
            disabled={disabled}
            onChange={(e) =>
              onChange(field.name, JSON.stringify(e.target.checked))
            }
          />
          <span>{value === true ? "条件成立" : "条件不成立"}</span>
        </label>
      );
    return (
      <label className="panel-action-field">
        <span>{field.label}</span>
        <input
          type={flag.type === "integer" ? "number" : "text"}
          step={flag.type === "integer" ? 1 : undefined}
          min={flag.type === "integer" ? -1000000 : undefined}
          max={flag.type === "integer" ? 1000000 : undefined}
          maxLength={200}
          disabled={disabled}
          value={
            typeof value === "number" || typeof value === "string" ? value : ""
          }
          onChange={(e) =>
            onChange(
              field.name,
              flag.type === "integer" && e.target.value === ""
                ? ""
                : JSON.stringify(
                    flag.type === "integer"
                      ? Number(e.target.value)
                      : e.target.value,
                  ),
            )
          }
        />
      </label>
    );
  }
  if (
    spec.kind === "publish_message" &&
    field.name === "speaker_id" &&
    !["npc", "investigator"].includes(String(values.speaker_kind))
  )
    return null;
  if (field.kind === "audience" || field.name.startsWith("audience_")) {
    if (field.name === "audience_investigator_ids") {
      const visible = String(values.audience_kind) === "investigators";
      if (!visible) return null;
    }
  }
  if (field.name === "target_id" || field.name === "target_kind") {
    // target 由下面统一的复合控件渲染。
    if (field.name === "target_kind") return null;
  }

  if (field.kind === "bool") {
    return (
      <label className="panel-action-field keeper-field-inline">
        <input
          type="checkbox"
          checked={values[field.name] === true}
          disabled={disabled}
          onChange={(event) => onChange(field.name, event.target.checked)}
        />
        <span>{field.label}</span>
      </label>
    );
  }

  if (field.kind === "enum") {
    return (
      <label className="panel-action-field">
        <span>{field.label}</span>
        <select
          value={String(values[field.name] ?? "")}
          disabled={disabled}
          onChange={(event) => {
            onChange(field.name, event.target.value);
            if (field.name === "speaker_kind") onChange("speaker_id", "");
          }}
        >
          {!field.required && (
            <option value="">
              {field.name === "activity" && spec.kind === "advance_time"
                ? "未指定（按等待计时）"
                : "未填写（不提交此项）"}
            </option>
          )}
          {(field.enumValues ?? []).map((value) => (
            <option key={value} value={value}>
              {field.name === "activity" && spec.kind === "advance_time"
                ? (TIME_ACTIVITY_LABELS[value] ?? value)
                : spec.kind === "publish_message" &&
                    field.name === "speaker_kind" &&
                    value === "keeper"
                  ? "守秘人旁白"
                  : (CHOICE_LABELS[value] ?? value)}
            </option>
          ))}
        </select>
      </label>
    );
  }

  if (field.kind === "target") {
    const options = [
      ...candidates.npcs.map((entry) => ({ ...entry, kind: "npc" })),
      ...candidates.investigators.map((entry) => ({
        ...entry,
        kind: "investigator",
      })),
      ...(candidates.objects ?? []).map((entry) => ({
        ...entry,
        kind: "scene_object",
      })),
    ];
    return (
      <>
        <label className="panel-action-field">
          <span>{field.label}</span>
          <select
            value={
              values.target_kind === "unresolved"
                ? "unresolved:"
                : `${String(values.target_kind ?? "")}:${String(values.target_id ?? "")}`
            }
            disabled={disabled}
            onChange={(event) => {
              const [kind, ...idParts] = event.target.value.split(":");
              const id = idParts.join(":"); // IDs may themselves contain namespaces.
              onChange("target_kind", kind || "");
              onChange("target_id", id || "");
            }}
          >
            <option value=":">不指定</option>
            {options.map((option) => (
              <option
                key={`${option.kind}:${option.id}`}
                value={`${option.kind}:${option.id}`}
              >
                {option.kind === "npc"
                  ? "人物"
                  : option.kind === "scene_object"
                    ? "场景物件"
                    : "调查员"}
                ·{option.name}
              </option>
            ))}
            <option value="unresolved:">描述其他对象…</option>
          </select>
        </label>
        {String(values.target_kind) === "unresolved" && (
          <label className="panel-action-field">
            <span>描述对象</span>
            <input
              type="text"
              value={String(values.target_id ?? "")}
              maxLength={160}
              disabled={disabled}
              onChange={(event) => onChange("target_id", event.target.value)}
            />
          </label>
        )}
      </>
    );
  }

  if (field.kind === "int") {
    return (
      <label className="panel-action-field">
        <span>{field.label}</span>
        <input
          type="number"
          value={String(values[field.name] ?? "")}
          min={field.min}
          max={field.max}
          disabled={disabled}
          title={field.help ?? undefined}
          onChange={(event) => onChange(field.name, event.target.value)}
        />
      </label>
    );
  }

  if (spec.kind === "publish_message" && field.name === "text") {
    return (
      <div className="keeper-narrative-field">
        <label className="panel-action-field">
          <span>{field.label}</span>
          <textarea
            id="keeper-field-text"
            value={String(values[field.name] ?? "")}
            rows={7}
            maxLength={field.maxLength}
            disabled={disabled}
            placeholder="描述眼前的场景，回应调查员的行动，或让人物开口说话……"
            onChange={(event) => onChange(field.name, event.target.value)}
          />
        </label>
        <span className="keeper-text-count">
          {String(values[field.name] ?? "").length} / {field.maxLength}
        </span>
      </div>
    );
  }

  if (field.multiline) {
    return (
      <div className="keeper-multiline-field">
        <label className="panel-action-field">
          <span>{field.label}</span>
          <textarea
            id={`keeper-field-${field.name}`}
            rows={3}
            maxLength={field.maxLength}
            value={String(values[field.name] ?? "")}
            disabled={disabled}
            aria-describedby={
              field.help ? `keeper-hint-${field.name}` : undefined
            }
            onChange={(event) => onChange(field.name, event.target.value)}
          />
        </label>
        {field.help && (
          <p id={`keeper-hint-${field.name}`} className="keeper-note">
            {field.help}
          </p>
        )}
        <span className="keeper-text-count">
          {String(values[field.name] ?? "").length} / {field.maxLength}
        </span>
      </div>
    );
  }

  const options =
    field.name === "speaker_id" && values.speaker_kind === "investigator"
      ? candidates.investigators
      : candidatesFor(field.candidate, candidates);
  // 提示文本放在 label 之外：label 的可访问名必须精确等于字段名，
  // 否则屏幕阅读器与 getByLabelText 都会读成整段提示。
  const hint = (() => {
    if (field.kind === "id_list") {
      return options.length
        ? `可选：${options.map((option) => `${option.name}(${option.id})`).join("、")}`
        : null;
    }
    if (
      options.length === 0 &&
      field.candidate &&
      (field.required || spec.kind === "grant_clue")
    ) {
      return "服务端尚未提供该字段的候选清单，请填写稳定 ID。";
    }
    return field.help ?? null;
  })();

  const withHint = (control: React.ReactNode) => (
    <div className="keeper-field">
      {control}
      {hint && <span className="panel-action-note">{hint}</span>}
    </div>
  );

  // Exact full skills of the selected PC, not lobby top-skills or another PC's
  // numbers. Manual input remains available for older servers without sheets.
  if (field.name === "skill") {
    const selected = keeperInvestigators.find(
      (sheet) => sheet.investigatorId === values.investigator_id,
    );
    const skillOptions = selected ? Object.entries(selected.skills) : [];
    return withHint(
      <label className="panel-action-field">
        <span>{field.label}</span>
        <input
          id={`keeper-field-${field.name}`}
          type="text"
          list="keeper-skill-options"
          value={String(values[field.name] ?? "")}
          maxLength={field.maxLength}
          disabled={disabled}
          title={field.help ?? undefined}
          onChange={(event) => onChange(field.name, event.target.value)}
        />
        <datalist id="keeper-skill-options">
          {skillOptions.map(([id, value]) => (
            <option key={id} value={id}>
              {`${id}（${value}%）`}
            </option>
          ))}
        </datalist>
      </label>,
    );
  }

  if (field.kind === "id_list") {
    const selected = String(values[field.name] ?? "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);
    return withHint(
      <div>
        {options.length > 0 && (
          <fieldset className="keeper-recipient-picker" disabled={disabled}>
            <legend>选择{field.label}</legend>
            {options.map((option) => (
              <label key={option.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(option.id)}
                  onChange={(event) =>
                    onChange(
                      field.name,
                      (event.target.checked
                        ? Array.from(new Set([...selected, option.id]))
                        : selected.filter((id) => id !== option.id)
                      ).join(","),
                    )
                  }
                />
                <span>{option.name}</span>
              </label>
            ))}
          </fieldset>
        )}
        <label className="panel-action-field">
          <span>{field.label}</span>
          <input
            type="text"
            value={String(values[field.name] ?? "")}
            disabled={disabled}
            placeholder={
              options.length
                ? options.map((o) => o.id).join(",")
                : "逗号分隔 ID"
            }
            onChange={(event) => onChange(field.name, event.target.value)}
          />
        </label>
      </div>,
    );
  }

  // 请求/线程 ID 的候选**天然不完整**（已终态、读档后按历史补记的对象不在当前列表里），
  // 所以这两种用 input + datalist：可挑可填，既不用手抄也不剥夺自由输入。
  // 其余候选（调查员/NPC/场景/线索/物品）是服务端完整投影，保持下拉。
  const freeFormId =
    field.candidate === "requests" || field.candidate === "threads";
  if (options.length > 0 && freeFormId) {
    const listId = `keeper-options-${field.name}`;
    return withHint(
      <label className="panel-action-field">
        <span>{field.label}</span>
        <input
          id={`keeper-field-${field.name}`}
          type="text"
          list={listId}
          aria-label={field.label}
          value={String(values[field.name] ?? "")}
          disabled={disabled}
          placeholder="可直接输入 ID，或从候选里挑"
          title={field.help ?? undefined}
          onChange={(event) => onChange(field.name, event.target.value)}
        />
        <datalist id={listId}>
          {options.map((option) => (
            <option key={option.id} value={option.id}>
              {`${option.name}（${option.id}）`}
            </option>
          ))}
        </datalist>
      </label>,
    );
  }

  if (options.length > 0) {
    return withHint(
      <label className="panel-action-field">
        <span>{field.label}</span>
        <select
          aria-label={field.label}
          value={String(values[field.name] ?? "")}
          disabled={disabled}
          onChange={(event) => onChange(field.name, event.target.value)}
        >
          <option value="">{field.required ? "请选择" : "不指定"}</option>
          {options.map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}（{option.id}）
            </option>
          ))}
        </select>
      </label>,
    );
  }

  return withHint(
    <label className="panel-action-field">
      <span>{field.label}</span>
      <input
        id={`keeper-field-${field.name}`}
        type="text"
        value={String(values[field.name] ?? "")}
        maxLength={field.maxLength}
        disabled={disabled}
        title={field.help ?? undefined}
        onChange={(event) => onChange(field.name, event.target.value)}
      />
    </label>,
  );
}

/**
 * M5：主持侧**只读**记忆查询（最小实现）。
 * 只做三件事：按角色/主题/文本查一次、显示选用的过滤条件、显示结果或失败/无结果。
 * 不做记忆编辑/删除，也不把记忆当权威世界状态；能力由服务端 `memory_query` 声明，
 * 真正的授权在服务端（玩家连接即使伪造帧也会被拒）。
 */
function MemoryQueryPanel({
  candidates,
  blocked,
}: {
  candidates: KeeperCandidates;
  blocked: string | null;
}) {
  const [characterId, setCharacterId] = useState("");
  const [topics, setTopics] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryState = useStructuredStore((state) => state.memoryQuery);
  const options = [
    ...candidates.investigators.map((entry) => ({
      ...entry,
      kind: "investigator",
    })),
    ...candidates.npcs.map((entry) => ({ ...entry, kind: "npc" })),
  ];
  const filters = queryState.filters;
  const filterSummary = [
    filters.character_id
      ? `角色：${options.find((entry) => entry.id === filters.character_id)?.name || "指定角色"}`
      : "全部角色",
    Array.isArray(filters.topics) && filters.topics.length
      ? `主题：${filters.topics.join("、")}`
      : "",
    typeof filters.text === "string" && filters.text
      ? `文本：${filters.text}`
      : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const submit = () => {
    const topicList = topics
      .split(/[,，]/)
      .map((entry) => entry.trim())
      .filter(Boolean);
    const result = sendMemoryQuery({
      ...(characterId ? { characterId } : {}),
      ...(topicList.length ? { topics: topicList } : {}),
      ...(text.trim() ? { text: text.trim() } : {}),
      limit: 10,
    });
    setError(result.ok ? null : result.reason);
  };

  return (
    <section
      className="keeper-memory-query"
      aria-label="记忆查询"
      data-testid="keeper-memory-query"
    >
      <h4 className="keeper-section-title">记忆查询（主持只读）</h4>
      <div className="keeper-command-form">
        <div className="keeper-field-slot" data-field="memory_character_id">
          <label className="panel-action-field">
            <span>角色</span>
            <select
              value={characterId}
              onChange={(event) => setCharacterId(event.target.value)}
            >
              <option value="">全部</option>
              {options.map((entry) => (
                <option key={`${entry.kind}:${entry.id}`} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="keeper-field-slot" data-field="memory_topics">
          <label className="panel-action-field">
            <span>主题（逗号分隔，最多6个，每个40字）</span>
            <input
              type="text"
              value={topics}
              onChange={(event) => setTopics(event.target.value)}
            />
          </label>
        </div>
        <div className="keeper-field-slot" data-field="memory_text">
          <label className="panel-action-field">
            <span>文本（最多200字）</span>
            <textarea
              rows={3}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
        </div>
        <button
          type="button"
          className="btn-ghost structured-btn"
          data-testid="keeper-memory-submit"
          disabled={queryState.status === "querying" || blocked !== null}
          title={blocked ?? "只读查询角色记忆，不改变世界状态"}
          onClick={submit}
        >
          {queryState.status === "querying" ? "查询中…" : "查询记忆"}
        </button>
      </div>
      {blocked && (
        <p className="structured-card-note" role="status">
          {blocked}
        </p>
      )}
      {error && (
        <p className="structured-card-error" role="alert">
          {error}
        </p>
      )}
      {queryState.status === "failed" && (
        <p
          className="structured-card-error"
          role="alert"
          data-testid="keeper-memory-failed"
        >
          {queryState.error}
        </p>
      )}
      {queryState.status === "done" && (
        <div data-testid="keeper-memory-results">
          <p className="structured-card-note">
            筛选：{filterSummary}｜命中 {queryState.entries.length} 条
            {queryState.truncated ? "（已截断）" : ""}
          </p>
          {queryState.entries.length === 0 ? (
            <p
              className="structured-card-detail"
              data-testid="keeper-memory-empty"
            >
              没有命中记忆：可能是该角色尚无记录，或过滤条件太窄。
            </p>
          ) : (
            <ul className="keeper-material-list">
              {queryState.entries.map((entry) => (
                <li key={entry.memoryId}>
                  <strong>
                    {CHOICE_LABELS[entry.knowledgeType] || "角色看法"}
                  </strong>
                  {`｜${options.find((option) => option.id === entry.characterId)?.name || "未命名角色"}｜${candidates.scenes.find((scene) => scene.id === entry.sceneId)?.name || (entry.sceneId ? "地点资料未提供" : "—")}｜`}
                  {entry.content}
                  {entry.topics.length
                    ? `（主题：${entry.topics.join("/")}）`
                    : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
