import { useEffect, useState } from "react";
import { CaseCharacterActions } from "./CaseCharacterActions";
import { CombatActionRequestButton } from "./CombatActionRequestButton";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";
import { COMBAT_ACTION_LABELS as ACTIONS } from "../../../protocol/combat";
import { CONDITION_LABELS } from "../../../protocol/conditions";
import {
  sendCombatResponse,
  structuredPlayerRequestReason,
} from "../../../structured-transport";

const OUTCOMES: Record<string, string> = {
  victory: "胜利",
  defeat: "失利",
  stalemate: "僵持结束",
  confrontation_resolved: "调查员对抗结束",
};

/** Matches the generated field-record concept; all live text remains DOM. */
export function CombatAndEndingCards() {
  const combat = useStructuredStore((s) => s.combat);
  const roll = useStructuredStore((s) => s.combatRoll);
  const decision = useStructuredStore((s) => s.combatDecision);
  const ending = useStructuredStore((s) => s.gameOver);
  const rewards = useStructuredStore((s) => s.caseSettlements);
  const results = useStructuredStore((s) => s.combatResults);
  const identity = useStructuredStore((s) => s.identity);
  const capabilities = useStructuredStore((s) => s.capabilities);
  const requests = useStructuredStore((s) => s.requests);
  useAppStore((s) => s.connection);
  const mode = useAppStore((s) => s.mode);
  const user = useOnlineStore((s) => s.user);
  const members = useOnlineStore((s) => s.members);
  const playMode = useOnlineStore((s) => s.playMode);
  const [sentId, setSentId] = useState("");
  const [error, setError] = useState("");
  const nonce = roll?.roll_id || decision?.id || "";
  useEffect(() => {
    setSentId("");
    setError("");
  }, [identity.worldId, nonce]);
  const request = requests[sentId];
  const sending =
    !!request && ["queued", "processing"].includes(request.status);
  const owner = roll?.investigator_id || decision?.responding_investigator_id;
  const canRespond = owner === identity.investigatorId && !!owner;
  const blocked = structuredPlayerRequestReason();
  const supported = capabilities.commands.includes(
    roll ? "combat_roll" : "combat_decide",
  );
  const names = new Map(
    (combat?.participants || []).map((p) => [p.id, p.name]),
  );
  const name = (id: string | null | undefined) =>
    id ? names.get(id) || "参战者" : "无目标";
  const send = (choice: string) => {
    const result = sendCombatResponse(
      roll ? "combat_roll" : "combat_decide",
      nonce,
      choice,
    );
    if (result.ok) {
      setSentId(result.requestId);
      setError("");
    } else setError(result.reason);
  };
  const ownRewards = rewards.filter(
    (r) => r.investigator_id === identity.investigatorId,
  );
  const endingReplayNote =
    mode === "local"
      ? "若想改变故事走向，可从存档管理读档或创建分支。"
      : playMode === "solo" &&
          members.some((m) => m.user_id === user?.id && m.role === "owner")
        ? capabilities.structuredSoloRestore
          ? "若想改变故事走向，可从存档管理读档或创建分支。"
          : "可从存档管理创建分支；当前服务器未开放结构化读档。"
        : "多人结构化房间不支持读档或创建分支，不会回滚其他玩家的进度。";
  const isKeeper =
    mode === "local" ||
    members.some((m) => m.user_id === user?.id && m.can_keeper === true);
  const visibleResults = results.filter(
    (r) => isKeeper || r.investigator_id === identity.investigatorId,
  );
  const outcomeName: Record<string, string> = {
    attacker_hit: "攻击命中",
    defender_hit: "反击命中",
    miss: "未命中",
    cancelled: "已取消",
  };
  return (
    <>
      {combat?.active && !ending && (
        <section
          className="combat-field-record archive-folder-panel archive-folder-panel--wide"
          data-testid="combat-field-record"
          aria-label="战斗现场记录"
        >
          <span className="combat-folder-label">现场记录</span>
          <header className="combat-record-heading">
            <h3>战斗 · 第 {combat.round || 1} 轮</h3>
            <span className="combat-wait-stamp">
              {roll || combat.awaiting_roll
                ? "等待掷骰"
                : decision || combat.awaiting_decision
                  ? "等待决定"
                  : "等待主持"}
            </span>
          </header>
          <p className="combat-record-current">
            当前行动者：{name(combat.current_actor)}
          </p>
          <details className="combat-participant-details">
            <summary>查看行动顺序与状态</summary>
            <ol>
              {(combat.turn_order || []).map((id) => {
                const p = combat.participants?.find((p) => p.id === id);
                return p ? (
                  <li
                    key={id}
                    aria-current={
                      combat.current_actor === id ? "step" : undefined
                    }
                  >
                    <span>
                      {p.name}
                      {combat.current_actor === id ? " · 当前行动" : ""}
                    </span>
                    <span>
                      HP {p.hp} / {p.max_hp}
                      {p.conditions.length
                        ? ` · ${p.conditions.map((condition) => CONDITION_LABELS[condition] || condition).join("、")}`
                        : ""}
                    </span>
                  </li>
                ) : null;
              })}
            </ol>
          </details>
          {roll && (
            <p className="combat-record-pending">
              {name(roll.actor_id)} → {name(roll.target_id)} ·{" "}
              {ACTIONS[roll.action_type]}
              <br />
              {roll.weapon_label && (
                <>
                  批准时选定：{roll.weapon_label}
                  <br />
                </>
              )}
              {roll.source === "pvp_defense" ? (
                "防御已选择，确认准备掷骰后等待攻击方；双方确认前不会产生骰点、伤害或消耗。"
              ) : roll.source === "pvp_attack" ? (
                "对方已确认准备掷骰，等待你确认；确认后双方骰点与资源统一结算。"
              ) : (
                <>尚未执行，等待{canRespond ? "你的" : "指定调查员的"}掷骰。</>
              )}
            </p>
          )}
          {decision && !roll && (
            <div className="combat-record-pending">
              <h4>{decision.title}</h4>
              <p>{decision.description}</p>
            </div>
          )}
          {canRespond && nonce && (
            <div className="combat-record-actions">
              {roll ? (
                <>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={sending || !!blocked || !supported}
                    onClick={() => send("roll")}
                  >
                    {sending
                      ? roll.source.startsWith("pvp_")
                        ? "等待确认…"
                        : "等待结算…"
                      : roll.source.startsWith("pvp_")
                        ? "确认掷骰"
                        : "掷骰"}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={sending || !!blocked || !supported}
                    onClick={() => send("cancel")}
                  >
                    取消动作
                  </button>
                </>
              ) : (
                decision?.options.map((o) => (
                  <button
                    key={o.id}
                    type="button"
                    className={
                      o.id === decision.default_option
                        ? "btn-primary"
                        : "btn-ghost"
                    }
                    disabled={sending || !!blocked || !supported}
                    title={o.description}
                    onClick={() => send(o.id)}
                  >
                    {o.label}
                  </button>
                ))
              )}
            </div>
          )}
          {canRespond && nonce && (blocked || !supported) && (
            <p className="structured-card-note">
              {blocked || "服务端尚未开放战斗响应，请联系主持。"}
            </p>
          )}
          {!nonce && !combat.awaiting_roll && !combat.awaiting_decision && (
            <CombatActionRequestButton
              key={`${identity.worldId}:${combat.encounter_id}:${combat.current_actor}`}
              combat={combat}
            />
          )}
          {(!nonce || !canRespond) && (
            <p className="structured-card-note">
              {nonce
                ? "此决定由对应调查员的玩家处理，主持不能代选或代掷。"
                : "等待主持批准下一步行动；当前没有可操作的旧掷骰按钮。"}
            </p>
          )}
          {(error || request?.errorMessage) && (
            <p role="alert" className="structured-card-error">
              {error || request?.errorMessage}
            </p>
          )}
        </section>
      )}
      {combat && !combat.active && combat.outcome && !ending && (
        <details className="combat-closed-record">
          <summary>
            上一场战斗 · {OUTCOMES[combat.outcome] || combat.outcome}
          </summary>
          <p>遭遇已结束，旧决定和掷骰按钮不再可用。</p>
        </details>
      )}
      {ending && (
        <section
          className="combat-ending-record archive-folder-panel archive-folder-panel--wide"
          data-testid="combat-ending-record"
          aria-label="案件结算"
        >
          <span className="combat-folder-label">案件结算</span>
          <h3>{ending.title}</h3>
          <p>{ending.summary || "本场游戏已结算。"}</p>
          {ownRewards.length ? (
            ownRewards.map((r) => (
              <div key={r.case.case_id}>
                <dl className="combat-reward-summary">
                  <div>
                    <dt>本案声望变化</dt>
                    <dd>
                      {r.case.reputation_delta >= 0 ? "+" : ""}
                      {r.case.reputation_delta}
                    </dd>
                  </div>
                  <div>
                    <dt>当前声望</dt>
                    <dd>{r.career.reputation}</dd>
                  </div>
                </dl>
                <CaseCharacterActions receipt={r} />
              </div>
            ))
          ) : (
            <p className="structured-card-note">奖励记录按调查员分别结算。</p>
          )}
          <p className="structured-card-note">
            本场不再接受新的调查行动。可以查看档案；{endingReplayNote}
            个人角色库只在你明确保存时创建新副本，不自动覆盖。
          </p>
        </section>
      )}
      {!!visibleResults.length && (
        <details
          className="combat-result-history"
          data-testid="combat-result-history"
        >
          <summary>查看已结算战斗记录（{visibleResults.length}）</summary>
          <ol>
            {[...visibleResults].reverse().map((r) => (
              <li key={r.roll_id}>
                <h4>
                  {ACTIONS[r.action_type]} · 第 {r.round} 轮 ·{" "}
                  {outcomeName[r.outcome] || "已结算"}
                </h4>
                {r.response === "cancel" ? (
                  <p>动作已取消，没有执行检定。</p>
                ) : (
                  r.rolls.map((roll) => (
                    <p key={roll.role}>
                      {name(roll.actor_id)}
                      {roll.role === "attack" ? "攻击" : "防御"}：d100=
                      {roll.roll} · {roll.level}
                    </p>
                  ))
                )}
                {r.damage && (
                  <p>
                    {name(r.damage.target_id)}受到 {r.damage.amount} 点伤害，HP{" "}
                    {r.damage.hp_before} → {r.damage.hp_after}。
                  </p>
                )}
                {r.response === "roll" && !r.damage && (
                  <p>本次没有造成伤害。</p>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
    </>
  );
}
