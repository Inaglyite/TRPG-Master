/**
 * KeeperConsole.tsx — 最小人类主持台。
 *
 * 职责：把主持操作变成明确的 `command_request`，并显示服务端返回的状态。
 * 不做任何“看起来成功”的本地推断：命令提交后只是 `queued`，结果以服务端
 * 事件为准；没有对应能力的按钮不渲染，而不是画一个假的入口。
 *
 * 授权：keeper 身份来自服务端投影（`session_snapshot.keeper`）。房主
 * （owner）不等于 keeper；只有服务端把当前用户标为 keeper 才显示控制台。
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { sendKeeperCommand } from "../../../structured-transport";
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
import { loadSave, openSavePanel, quickSave } from "../../../panels";
import { useAppStore } from "../../../state/app-store";
import { structuredUnavailableReason } from "../../../protocol/structured";
import { sendMemoryQuery } from "../../../structured-transport";
import {
  activeRequests,
  useStructuredStore,
} from "../../../state/structured-store";
import { useOnlineStore } from "../../../state/online-store";

/** keeper 判据：服务端投影为准，本地单机允许 keeperUserId 为空。 */
/** 旧世界名册缺失时 public_investigator_roster 的兜底 id（不是真实调查员）。 */
const LEGACY_PLACEHOLDER_ID = "legacy-pc";

export function keeperAuthorized(
  keeperUserId: string | null,
  keeperMode: string | null,
  currentUserId: string | null,
  localMode = false,
): boolean {
  if (!keeperMode) return false;
  if (currentUserId) {
    // 有账号身份：必须与服务端当前的 keeper 控制者一致（房主 ≠ keeper）。
    // 房间尚未把 can_keeper 下发给客户端，因此 keeper 为 null 时前端不显示控制台，
    // 服务端的 keeper 校验仍是最终边界（见前端契约文档待办）。
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
  const items = useStructuredStore((state) => state.items);
  const keeperMaterial = useStructuredStore((state) => state.keeperMaterial);
  const requestsMap = useStructuredStore((state) => state.requests);
  const requestOrder = useStructuredStore((state) => state.requestOrder);
  const currentUserId = useOnlineStore((state) => state.user?.id ?? null);

  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<Element | null>(null);
  const [activeKind, setActiveKind] = useState<string>("");
  const [values, setValues] = useState<FieldValues>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [feedback, setFeedback] = useState<string>("");

  const appMode = useAppStore((state) => state.mode);
  const roomOwner = useOnlineStore((state) => {
    const uid = state.user?.id;
    return (
      uid != null &&
      state.members.some(
        (member) => member.user_id === uid && member.role === "owner",
      )
    );
  });
  const roomStructured = useOnlineStore(
    (state) =>
      (state.roomMetadata as { execution_profile?: string } | null)
        ?.execution_profile === "structured_v1",
  );
  // 房间尚未把 can_keeper 下发给客户端：结构化房间里房主即创建者，
  // 服务端在创建世界时已给创建者 can_keeper，因此这里以“结构化房间 + 房主”
  // 作为客户端可见的 keeper 判据；命令被拒时服务端返回 keeper_required，
  // 由状态卡如实显示。本地单机沿用同一条规则（见 keeperAuthorized）。
  const authorized =
    keeperAuthorized(
      identity.keeperUserId,
      identity.keeperMode,
      currentUserId,
      appMode === "local",
    ) ||
    (appMode === "online" && roomStructured && roomOwner);
  const blocked = structuredUnavailableReason(capabilities, protocolNotice);

  const roomInvestigators = useOnlineStore((state) => state.roomInvestigators);
  const roomCharacterOptions = useOnlineStore(
    (state) => state.characterOptions,
  );
  const roomMembers = useOnlineStore((state) => state.members);
  const candidates: KeeperCandidates = useMemo(
    () => ({
      // 房间调查员名单来自房间镜像与成员信息：keeper 需要它指定线索接收者、
      // 检定对象与 HP/SAN 目标。房间快照的 targets 可能不含调查员，不能只靠它。
      investigators: [
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
      npcs: targets
        .filter((target) => target.kind === "npc")
        .map((target) => ({ id: target.id, name: target.name })),
      scenes: destinations.map((scene) => ({ id: scene.id, name: scene.name })),
      clues: clues.map((clue) => ({
        id: clue.id,
        name: clue.text.slice(0, 40) || clue.id,
      })),
      items: items.map((item) => ({
        id: item.id,
        name: `${item.label} ×${item.quantity}`,
      })),
      // 素材与玩家请求由服务端投影；M0 未提供时保持空列表并说明。
      assets: [],
      requests: [],
    }),
    [targets, destinations, clues, items, roomInvestigators, roomMembers],
  );

  const pending = useMemo(
    () => activeRequests({ requests: requestsMap, requestOrder }),
    [requestsMap, requestOrder],
  );

  const spec = findKeeperCommand(activeKind);

  const submit = () => {
    if (!spec) return;
    const problems = validateKeeperFields(spec, values);
    setErrors(problems);
    if (problems.length) return;
    if (blocked) {
      setErrors([blocked]);
      return;
    }
    const payload = buildKeeperPayload(spec, values);
    const result = sendKeeperCommand(spec.kind, payload);
    if (!result.ok) {
      setErrors([result.reason]);
      setFeedback("");
      return;
    }
    setFeedback(
      `已提交命令 ${spec.kind}（command_id=${result.requestId}）。命令已被服务端接收不代表执行成功，结果以事件为准。`,
    );
  };

  // 键盘可用：打开时聚焦表单首个控件，Escape 关闭，关闭后焦点回到触发按钮。
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement;
    const first = dialogRef.current?.querySelector<HTMLElement>(
      "button, input, select, textarea",
    );
    first?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      const restore = restoreFocusRef.current;
      if (restore instanceof HTMLElement) restore.focus();
    };
  }, [open]);

  if (!capabilities.keeperConsole && !protocolNotice) return null;

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
              <h3 id="keeper-console-title">主持台</h3>
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
              {!authorized && (
                <p className="online-notice" data-testid="keeper-unauthorized">
                  你不是本场主持。房主身份不等于主持权限，需要服务端把 keeper
                  身份授予你才会显示主持操作。
                </p>
              )}

              <section aria-label="待处理行动">
                <h4 className="keeper-section-title">待处理行动</h4>
                {pending.length === 0 ? (
                  <p className="clue-empty">暂无待处理请求</p>
                ) : (
                  <ul className="keeper-pending-list">
                    {pending.map((request) => (
                      <li key={request.requestId}>
                        <span className="keeper-pending-label">
                          {request.label}
                        </span>
                        <code className="keeper-pending-id">
                          {request.requestId}
                        </code>
                        <span
                          className={`structured-badge structured-badge--${request.status}`}
                        >
                          {request.status}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section aria-label="授权资料">
                <h4 className="keeper-section-title">授权模组资料</h4>
                <p className="keeper-note">
                  这是**服务端按 keeper 身份过滤后**下发的资料投影： keeper
                  拿到完整线索登记表与物品 ID/数量，玩家只拿到自己那条。
                  主持秘密（未公开场景文档、NPC 私设）需要服务端的 keeper
                  查询出口， M0 未定义该出口；服务端一旦在快照里给出{" "}
                  <code>keeper_material</code>， 这里会直接显示。
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
                {keeperMaterial.length > 0 ? (
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
                ) : (
                  <p
                    className="keeper-note"
                    data-testid="keeper-material-missing"
                  >
                    服务端未提供主持专属资料条目（<code>keeper_material</code>
                    ）。 这里不显示、也不假装可读。
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

              {capabilities.memoryQuery && (
                <MemoryQueryPanel candidates={candidates} />
              )}
              <section aria-label="存档与续团">
                <h4 className="keeper-section-title">存档与续团</h4>
                <p className="keeper-note">
                  主持可以直接存档、读档或打开存档管理；服务端仍会按房间权限复核
                  （多人房间的存档操作是房主权限）。
                </p>
                <div className="structured-card-actions">
                  <button
                    type="button"
                    className="btn-ghost structured-btn"
                    data-testid="keeper-save"
                    onClick={() => quickSave()}
                  >
                    快速存档
                  </button>
                  <button
                    type="button"
                    className="btn-ghost structured-btn"
                    data-testid="keeper-load"
                    onClick={() => loadSave("slot_000")}
                  >
                    读取自动存档
                  </button>
                  <button
                    type="button"
                    className="btn-ghost structured-btn"
                    data-testid="keeper-save-panel"
                    onClick={() => openSavePanel("manage")}
                  >
                    存档管理
                  </button>
                </div>
              </section>

              <section aria-label="主持操作">
                <h4 className="keeper-section-title">主持操作</h4>
                <div className="keeper-command-groups">
                  {KEEPER_COMMANDS.map((command) => {
                    const disabled =
                      blocked !== null ||
                      !authorized ||
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
                          setActiveKind(command.kind);
                          setValues(emptyKeeperValues(command));
                          setErrors([]);
                          setFeedback("");
                        }}
                      >
                        {command.label}
                      </button>
                    );
                  })}
                </div>
              </section>

              {spec && (
                <section aria-label="命令表单" className="keeper-command-form">
                  <h4 className="keeper-section-title">{spec.label}</h4>
                  {spec.help && <p className="keeper-note">{spec.help}</p>}
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
                        candidates={candidates}
                        roomCharacterOptions={roomCharacterOptions}
                        disabled={blocked !== null || !authorized}
                        onChange={(name, value) =>
                          setValues((current) => ({
                            ...current,
                            [name]: value,
                          }))
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
                  {feedback && <p className="keeper-feedback">{feedback}</p>}
                  <div className="structured-card-actions">
                    <button
                      type="button"
                      className="btn-primary structured-btn"
                      data-testid="keeper-submit"
                      disabled={blocked !== null || !authorized}
                      title={blocked ?? "提交主持命令"}
                      onClick={submit}
                    >
                      提交命令
                    </button>
                  </div>
                </section>
              )}
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
  roomCharacterOptions,
  disabled,
  onChange,
}: {
  field: CommandField;
  spec: KeeperCommandSpec;
  values: FieldValues;
  candidates: KeeperCandidates;
  roomCharacterOptions: readonly unknown[];
  disabled: boolean;
  onChange: (name: string, value: string | number | boolean) => void;
}) {
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
          onChange={(event) => onChange(field.name, event.target.value)}
        >
          {(field.enumValues ?? []).map((value) => (
            <option key={value} value={value}>
              {value}
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
    ];
    return (
      <>
        <label className="panel-action-field">
          <span>{field.label}</span>
          <select
            value={`${String(values.target_kind ?? "")}:${String(values.target_id ?? "")}`}
            disabled={disabled}
            onChange={(event) => {
              const [kind, id] = event.target.value.split(":");
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
                {option.kind === "npc" ? "人物" : "调查员"}·{option.name}
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

  const options = candidatesFor(field.candidate, candidates);
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

  // 技能字段：给出房间里各角色卡上真实存在的技能键（服务端按技能键校验，
  // 随便写一个中文技能名会被 invalid_action 拒掉）。
  if (field.name === "skill") {
    const skillOptions = Array.from(
      new Map(
        roomCharacterOptions
          .flatMap((option) => {
            const skills = (option as { top_skills?: unknown } | null)
              ?.top_skills;
            return Array.isArray(skills)
              ? (skills as { id?: unknown; value?: unknown }[])
              : [];
          })
          .flatMap((skill) =>
            typeof skill?.id === "string"
              ? [[skill.id, Number(skill?.value ?? 0)] as const]
              : [],
          ),
      ).entries(),
    );
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
    return withHint(
      <label className="panel-action-field">
        <span>{field.label}</span>
        <input
          type="text"
          value={String(values[field.name] ?? "")}
          disabled={disabled}
          placeholder={
            options.length ? options.map((o) => o.id).join(",") : "逗号分隔 ID"
          }
          onChange={(event) => onChange(field.name, event.target.value)}
        />
      </label>,
    );
  }

  if (options.length > 0) {
    return withHint(
      <label className="panel-action-field">
        <span>{field.label}</span>
        <select
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
function MemoryQueryPanel({ candidates }: { candidates: KeeperCandidates }) {
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

  const submit = () => {
    const topicList = topics
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean)
      .slice(0, 6);
    const result = sendMemoryQuery({
      ...(characterId ? { characterId } : {}),
      ...(topicList.length ? { topics: topicList } : {}),
      ...(text.trim() ? { text: text.trim().slice(0, 20) } : {}),
      limit: 10,
    });
    setError(result.ok ? null : result.reason);
  };

  return (
    <section aria-label="记忆查询" data-testid="keeper-memory-query">
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
            <span>主题（逗号分隔，≤6）</span>
            <input
              type="text"
              value={topics}
              onChange={(event) => setTopics(event.target.value)}
            />
          </label>
        </div>
        <div className="keeper-field-slot" data-field="memory_text">
          <label className="panel-action-field">
            <span>文本（≤20 字）</span>
            <input
              type="text"
              value={text}
              maxLength={20}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
        </div>
        <button
          type="button"
          className="btn-ghost structured-btn"
          data-testid="keeper-memory-submit"
          disabled={queryState.status === "querying"}
          onClick={submit}
        >
          {queryState.status === "querying" ? "查询中…" : "查询记忆"}
        </button>
      </div>
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
            过滤：{JSON.stringify(queryState.filters)}｜命中{" "}
            {queryState.entries.length} 条
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
                  <strong>{entry.knowledgeType}</strong>
                  {`｜${entry.characterId}｜${entry.sceneId || "—"}｜`}
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
