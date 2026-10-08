import { CONDITION_LABELS } from "../../../protocol/conditions";
import type { FieldValues } from "../../../protocol/keeper-commands";
import type { KeeperInvestigator } from "../../../state/structured-store";

export function conditionRecordProblem(
  sheet: KeeperInvestigator | undefined,
  values: FieldValues,
): string | null {
  if (!sheet) return "尚未收到该调查员的主持角色卡，请等待同步后再核对。";
  const condition = String(values.condition || "");
  const present = sheet.conditions.includes(condition);
  if (typeof values.expected_present !== "boolean") return "请先核对当前记录。";
  if (values.expected_present !== present)
    return "记录已变化，请核对当前记录后重新确认依据。";
  if (condition === "dead" && values.operation === "remove")
    return "死亡不能解除；如需回滚请读档或创建分支。";
  if (
    sheet.conditions.includes("dead") &&
    !(condition === "dead" && values.operation === "add")
  )
    return "不能通过本工具改变已死亡调查员的伤势。";
  if (
    condition === "dead" &&
    !present &&
    values.operation === "add" &&
    (sheet.hp === null || sheet.hp > 0)
  )
    return "请先明确结算生命值，HP 归零后才能记录死亡。";
  if (
    present &&
    values.operation === "remove" &&
    ["dying", "unconscious"].includes(condition) &&
    (sheet.hp === null || sheet.hp <= 0)
  )
    return "请先结算恢复生命；HP 大于 0 后才能解除濒死或昏迷。";
  return null;
}

/** A small opaque paper reference; the rest of the form stays dark. */
export function ConditionRecordReference({
  sheet,
}: {
  sheet: KeeperInvestigator | undefined;
}) {
  return (
    <aside className="keeper-condition-reference" aria-label="人物状态参考">
      <small>人类主持 · 仅主持可见</small>
      {sheet ? (
        <>
          <strong>{sheet.name}</strong>
          <span>
            HP {sheet.hp ?? "未同步"} / {sheet.maxHp ?? "未同步"}
          </span>
          <p>
            已记录：
            {sheet.conditions.length
              ? sheet.conditions
                  .map((entry) => CONDITION_LABELS[entry] || entry)
                  .join("、")
              : "无状态标记"}
          </p>
        </>
      ) : (
        <p>选择调查员后核对角色卡。不根据叙事推断伤势。</p>
      )}
    </aside>
  );
}

export function ConditionPreviousRecord({
  sheet,
  condition,
  previous,
  disabled,
  onRefresh,
}: {
  sheet: KeeperInvestigator | undefined;
  condition: string;
  previous: string | number | boolean | undefined;
  disabled: boolean;
  onRefresh: (present: boolean) => void;
}) {
  const stale =
    sheet &&
    typeof previous === "boolean" &&
    previous !== sheet.conditions.includes(condition);
  return (
    <div className="keeper-condition-previous">
      <span>
        本次核对：
        <output aria-label="本次核对的状态">
          {typeof previous === "boolean"
            ? previous
              ? "存在"
              : "未记录"
            : "待核对"}
        </output>
        {stale && <small role="status">记录已变化，请重新核对</small>}
      </span>
      <button
        type="button"
        className="btn-ghost structured-btn"
        disabled={disabled || !sheet || !condition}
        onClick={() => sheet && onRefresh(sheet.conditions.includes(condition))}
      >
        核对当前记录
      </button>
    </div>
  );
}
