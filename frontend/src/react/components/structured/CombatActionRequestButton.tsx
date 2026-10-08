import { useState } from "react";
import { createPortal } from "react-dom";
import {
  COMBAT_ACTIONS,
  COMBAT_ACTION_LABELS,
  type CombatState,
} from "../../../protocol/combat";
import { useStructuredStore } from "../../../state/structured-store";
import {
  sendStructuredAction,
  structuredPlayerRequestReason,
} from "../../../structured-transport";
import { CompactGameDialog } from "../CompactGameDialog";

/** Declares one typed intent; neither the button nor the form executes rules. */
export function CombatActionRequestButton({ combat }: { combat: CombatState }) {
  const identity = useStructuredStore((s) => s.identity);
  const supported = useStructuredStore(
    (s) => s.capabilities.combatActionRequest,
  );
  const requests = useStructuredStore((s) => s.requests);
  const items = useStructuredStore((s) => s.items);
  const supportsWeaponId = useStructuredStore(
    (s) => s.capabilities.combatWeaponItemId,
  );
  const [open, setOpen] = useState(false);
  const [actionType, setActionType] =
    useState<(typeof COMBAT_ACTIONS)[number]>("melee");
  const [targetId, setTargetId] = useState("");
  const [weaponId, setWeaponId] = useState("");
  const [approach, setApproach] = useState("");
  const [sentId, setSentId] = useState("");
  const [error, setError] = useState("");
  const last = requests[sentId];
  const submittedNote =
    last?.status === "declined"
      ? last.detail || "主持未批准本次动作，可修改后重新申报。"
      : last && ["completed", "cancelled"].includes(last.status)
        ? "本次申报已收尾。"
        : sentId && !last?.errorMessage
          ? "已提交申报，等待主持审核；提交本身不产生骰点、消耗或伤害。"
          : "";
  const actor = combat.participants?.find(
    (p) => p.id === identity.investigatorId,
  );
  if (!actor || actor.kind !== "pc" || combat.current_actor !== actor.id)
    return null;
  const needsTarget = ["melee", "firearm", "threat"].includes(actionType);
  const usesWeapon = ["melee", "firearm", "threat"].includes(actionType);
  const ownItems = items.filter((item) => item.quantity > 0);
  const chosenWeapon = ownItems.find((item) => item.id === weaponId);
  const targets = (combat.participants || []).filter(
    (p) => p.id !== actor.id && p.hp > 0 && !p.conditions.includes("dead"),
  );
  const pending = Object.values(requests).some((r) => {
    const action =
      (
        r.payload as {
          action?: { kind?: string; encounter_id?: string };
        } | null
      )?.action || r.keeperAction;
    const owner =
      (r.payload as { investigator_id?: string } | null)?.investigator_id ||
      r.investigatorId;
    return (
      owner === identity.investigatorId &&
      r.kind === "combat" &&
      !["completed", "declined", "cancelled", "failed"].includes(r.status) &&
      action?.kind === "combat" &&
      "encounter_id" in action &&
      action.encounter_id === combat.encounter_id
    );
  });
  const blocked =
    structuredPlayerRequestReason() ||
    (!supported ? "服务端尚未开放战斗申报。" : null) ||
    (!combat.encounter_id ? "遭遇标识尚未同步，请刷新或联系主持。" : null) ||
    (combat.awaiting_roll || combat.awaiting_decision
      ? "请先处理当前决定或掷骰。"
      : null) ||
    (actor.hp <= 0 ||
    actor.conditions.some((c) => ["dead", "dying", "unconscious"].includes(c))
      ? "这名调查员当前无法行动。"
      : null) ||
    (pending ? "已有本场战斗申报，等待主持处理；可在待办中取消。" : null);
  const invalidTarget = targetId && !targets.some((p) => p.id === targetId);
  const invalid =
    blocked ||
    (invalidTarget ? "目标状态已变化，请重新选择。" : null) ||
    (weaponId && !chosenWeapon ? "所选物品已失效，请重新选择。" : null) ||
    (actionType === "firearm" && !supportsWeaponId
      ? "服务端还不支持指定射击物品，请更新服务端或请主持处理。"
      : null) ||
    (actionType === "firearm" && !weaponId
      ? "请选择本次射击使用的持有物品。"
      : null) ||
    (needsTarget && !targetId ? "请选择本场遭遇中的目标。" : null);
  const submit = () => {
    if (invalid || !combat.encounter_id) return;
    const result = sendStructuredAction(
      {
        kind: "combat",
        encounter_id: combat.encounter_id,
        action_type: actionType,
        target_id: targetId || null,
        ...(usesWeapon && weaponId ? { weapon_item_id: weaponId } : {}),
        ...(approach.trim() ? { approach: approach.trim() } : {}),
      },
      `申报${COMBAT_ACTION_LABELS[actionType]}${targetId ? ` · ${targets.find((p) => p.id === targetId)?.name || targetId}` : ""}`,
    );
    if (!result.ok) {
      setError(result.reason);
      return;
    }
    setSentId(result.requestId);
    setError("");
    setOpen(false);
  };
  return (
    <>
      <div className="combat-record-actions">
        <button
          type="button"
          className="btn-primary"
          disabled={!!blocked}
          title={blocked || undefined}
          onClick={() => {
            setError("");
            setOpen(true);
          }}
        >
          申报战斗动作
        </button>
      </div>
      {(blocked || submittedNote) && (
        <p className="structured-card-note">{blocked || submittedNote}</p>
      )}
      {(error || requests[sentId]?.errorMessage) && (
        <p role="alert" className="structured-card-error">
          {error || requests[sentId]?.errorMessage}
        </p>
      )}
      {open &&
        createPortal(
          <CompactGameDialog
            id="combat-action-request"
            title="申报战斗动作"
            closeLabel="取消战斗申报"
            onClose={() => setOpen(false)}
            footer={
              <>
                <button
                  type="button"
                  className="btn-ghost panel-action-cancel"
                  onClick={() => setOpen(false)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="btn-primary panel-action-confirm"
                  disabled={!!invalid}
                  onClick={submit}
                >
                  提交申报
                </button>
              </>
            }
          >
            <p className="panel-action-subject">
              行动者：<strong>{actor.name}</strong> · 第 {combat.round || 1} 轮
            </p>
            <label className="panel-action-field">
              <span>动作</span>
              <select
                aria-label="动作"
                value={actionType}
                onChange={(e) => {
                  setActionType(e.target.value as typeof actionType);
                  setWeaponId("");
                }}
              >
                {COMBAT_ACTIONS.map((type) => (
                  <option key={type} value={type}>
                    {COMBAT_ACTION_LABELS[type]}
                  </option>
                ))}
              </select>
            </label>
            <label className="panel-action-field">
              <span>目标</span>
              <select
                aria-label="目标"
                value={targetId}
                onChange={(e) => setTargetId(e.target.value)}
              >
                <option value="">
                  {needsTarget ? "请选择参战者" : "不指定参战目标"}
                </option>
                {targets.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            {usesWeapon && (
              <label className="panel-action-field">
                <span>使用的持有物品</span>
                <select
                  aria-label="使用的持有物品"
                  value={weaponId}
                  disabled={!supportsWeaponId}
                  onChange={(e) => setWeaponId(e.target.value)}
                >
                  <option value="">
                    {actionType === "firearm"
                      ? "请选择本人的持有物品"
                      : "未指定（由主持核对）"}
                  </option>
                  {ownItems.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label} · ×{item.quantity} · {item.id.slice(-8)}
                    </option>
                  ))}
                </select>
                {chosenWeapon && (
                  <span className="combat-weapon-binding-note">
                    本件编号：<code>{chosenWeapon.id}</code>
                    {chosenWeapon.quantity > 1
                      ? "；使用本堆叠中的一件，不会改动其他件的余弹。"
                      : ""}
                  </span>
                )}
              </label>
            )}
            <label className="panel-action-field">
              <span>补充做法（选填）</span>
              <textarea
                value={approach}
                maxLength={200}
                rows={3}
                onChange={(e) => setApproach(e.target.value)}
              />
            </label>
            <p className="panel-action-note">
              这里只申报一次动作。主持核验技能与持有武器后准备行动；需要决定或检定时，再由对应玩家响应。战术移动不等于切换场景。
            </p>
            {invalid && <p className="panel-action-note">{invalid}</p>}
            {error && (
              <p role="alert" className="structured-card-error">
                {error}
              </p>
            )}
          </CompactGameDialog>,
          document.body,
        )}
    </>
  );
}
