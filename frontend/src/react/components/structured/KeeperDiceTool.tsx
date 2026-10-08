import { useEffect, useState } from "react";
import {
  keeperDiceProblem,
  type KeeperRollReceipt,
} from "../../../protocol/keeper-dice";
import {
  keeperCommandBlockReason,
  sendKeeperCommand,
} from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";
import { CompactGameDialog } from "../CompactGameDialog";

function useKeeperDiceAccess() {
  const state = useStructuredStore();
  const online = useOnlineStore();
  const mode = useAppStore((s) => s.mode);
  useAppStore((s) => s.connection);
  const authorized =
    mode === "online"
      ? online.members.some(
          (m) => m.user_id === online.user?.id && m.can_keeper === true,
        )
      : mode === "local" && Boolean(state.identity.keeperMode);
  return {
    state,
    authorized,
    active:
      state.identity.keeperMode === "human" &&
      (mode === "local" ||
        !state.identity.keeperUserId ||
        state.identity.keeperUserId === online.user?.id),
  };
}

function Receipt({ roll }: { roll: KeeperRollReceipt }) {
  return (
    <div
      className="keeper-dice-receipt"
      data-testid="keeper-dice-receipt"
      data-command-id={roll.command_id}
    >
      <span>
        主持普通骰 · {roll.visibility === "keeper" ? "仅主持可见" : "公开"}
      </span>
      <strong>
        {roll.expression} → {roll.total}
      </strong>
      <small>只记录随机数，不结算技能、伤害或剧情。</small>
    </div>
  );
}

export function KeeperDiceHistory() {
  const { state, authorized } = useKeeperDiceAccess();
  const rolls = state.keeperRolls.filter(
    (r) => r.visibility === "public" || authorized,
  );
  if (!rolls.length) return null;
  return (
    <section aria-label="主持普通骰记录" className="keeper-dice-history">
      <Receipt roll={rolls[rolls.length - 1]} />
      {rolls.length > 1 && (
        <details>
          <summary>较早骰点（{rolls.length - 1}）</summary>
          {rolls.slice(0, -1).map((roll) => (
            <Receipt key={roll.command_id} roll={roll} />
          ))}
        </details>
      )}
    </section>
  );
}

export function KeeperDiceTool() {
  const { state, authorized, active } = useKeeperDiceAccess();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setOpen(false);
  }, [state.identity.worldId, state.historyGeneration]);
  useEffect(() => {
    if (!authorized) setOpen(false);
  }, [authorized]);
  if (!authorized) return null;
  const blocked =
    keeperCommandBlockReason("keeper_roll") ??
    (active ? null : "请先取得人类主持控制权。");
  return (
    <>
      <button
        type="button"
        className="btn-ghost structured-tool-btn"
        data-testid="btn-keeper-dice"
        disabled={blocked !== null}
        title={blocked || "主持普通骰，不需要认领调查员"}
        onClick={() => setOpen(true)}
      >
        主持骰
      </button>
      {open && (
        <KeeperDiceDialog blocked={blocked} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

function KeeperDiceDialog({
  blocked,
  onClose,
}: {
  blocked: string | null;
  onClose: () => void;
}) {
  const [spec, setSpec] = useState("1d100");
  const [visibility, setVisibility] = useState<"keeper" | "public">("keeper");
  const [error, setError] = useState<string | null>(null);
  const invalid = blocked || keeperDiceProblem(spec);
  const submit = () => {
    if (invalid) {
      setError(invalid);
      return;
    }
    const result = sendKeeperCommand("keeper_roll", { spec, visibility });
    if (result.ok) onClose();
    else setError(result.reason);
  };
  return (
    <CompactGameDialog
      id="keeper-dice-panel"
      title="主持普通骰"
      closeLabel="关闭主持普通骰"
      context={<span>不需要认领调查员</span>}
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            className="btn-ghost panel-action-cancel"
            onClick={onClose}
          >
            关闭
          </button>
          <button
            type="button"
            className="btn-primary panel-action-confirm"
            data-testid="keeper-dice-submit"
            disabled={invalid !== null}
            title={invalid || "主动掷一次普通骰"}
            onClick={submit}
          >
            掷骰
          </button>
        </>
      }
    >
      <label className="panel-action-field">
        <span>骰式</span>
        <input
          type="text"
          value={spec}
          maxLength={40}
          onChange={(e) => {
            setSpec(e.target.value);
            setError(null);
          }}
        />
      </label>
      <label className="panel-action-field">
        <span>接收范围</span>
        <select
          value={visibility}
          onChange={(e) => {
            setVisibility(e.target.value as "keeper" | "public");
            setError(null);
          }}
        >
          <option value="keeper">仅主持</option>
          <option value="public">公开</option>
        </select>
      </label>
      <p className="panel-action-note">
        公开结果会发给所有参与者；仅主持结果不会发给玩家。只产生随机数，不结算技能、伤害或剧情，不能代掷玩家待检定。
      </p>
      <p className="panel-action-note">
        1–10颗、2–100面，修正值最多±999；例如 2d6+3。
      </p>
      {invalid && (
        <p role="status" className="panel-action-error">
          {invalid}
        </p>
      )}
      {error && (
        <p role="alert" className="panel-action-error">
          {error}
        </p>
      )}
    </CompactGameDialog>
  );
}
