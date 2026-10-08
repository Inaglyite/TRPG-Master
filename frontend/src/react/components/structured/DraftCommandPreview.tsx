import {
  findKeeperCommand,
  type CandidateSource,
} from "../../../protocol/keeper-commands";
import { useStructuredStore } from "../../../state/structured-store";
import { TIME_ACTIVITY_LABELS } from "../../../protocol/time-activity";
import {
  HOLDER_LABEL,
  holderValue,
  holderSchema,
} from "../../../protocol/item-holders";

const labels: Record<string, string> = {
  melee: "近战",
  firearm: "射击",
  threat: "威胁",
  move: "移动",
  other: "其他动作",
  completed: "已完成",
  declined: "拒绝",
  cancelled: "取消",
  paused: "暂停",
  awaiting_player: "等待玩家回应",
  success: "成功",
  failure: "失败",
  not_executed: "尚未执行",
  regular: "普通",
  hard: "困难",
  extreme: "极难",
  good: "好结局",
  neutral: "中性结局",
  bad: "坏结局",
  secret: "隐藏结局",
  dodge: "闪避",
  fight_back: "反击",
  no_defense: "不防御",
  take_cover: "寻找掩体",
};

/** Read the command payload, not narrative keywords; raw parameters remain available. */
export function DraftCommandPreview({
  command,
  index,
}: {
  command: { kind: string; payload: Record<string, unknown> };
  index: number;
}) {
  const state = useStructuredStore();
  const investigators = new Map([
    ...state.targets
      .filter((t) => t.kind === "investigator")
      .map((t) => [t.id, t.name] as const),
    ...state.keeperInvestigators.map(
      (i) => [i.investigatorId, i.name] as const,
    ),
  ]);
  const npcs = new Map(
    state.targets
      .filter((t) => t.kind === "npc")
      .map((t) => [t.id, t.name] as const),
  );
  const sources: Partial<Record<CandidateSource, Map<string, string>>> = {
    holders: new Map(
      (state.keeperProgress?.holdings?.holders || []).map((h) => [
        holderValue(h),
        `${HOLDER_LABEL[h.kind]} · ${h.name}`,
      ]),
    ),
    investigators,
    npcs,
    combatants: new Map([
      ...npcs,
      ...investigators,
      ...(state.combat?.participants || []).map((p) => [p.id, p.name] as const),
    ]),
    scenes: new Map(state.destinations.map((d) => [d.id, d.name])),
    items: new Map([
      ...(state.keeperProgress?.holdings?.items || []).map(
        (i) => [i.id, i.label] as const,
      ),
      ...state.items.map((i) => [i.id, i.label] as const),
      ...state.keeperInvestigators.flatMap((i) =>
        i.inventory.map((item) => [item.id, item.label] as const),
      ),
    ]),
    assets: new Map(state.keeperAssets.map((a) => [a.id, a.label])),
    requests: new Map(
      Object.entries(state.requests).map(([id, r]) => [id, r.label]),
    ),
    threads: new Map(
      Object.entries(state.interactions).map(([id, t]) => [
        id,
        t.pendingAction.note || id,
      ]),
    ),
  };
  const spec = findKeeperCommand(command.kind);
  const value = (raw: unknown, fieldName: string): string => {
    if (raw === null) return "空值";
    if (typeof raw === "boolean") return raw ? "是" : "否";
    if (typeof raw === "number") return String(raw);
    if (typeof raw === "string") {
      if (command.kind === "advance_time" && fieldName === "activity")
        return TIME_ACTIVITY_LABELS[raw] || raw;
      const candidate = spec?.fields.find(
        (f) => f.name === fieldName,
      )?.candidate;
      if (fieldName === "weapon_item_id") {
        const label = sources.items?.get(raw);
        return label ? `${label} · ${raw}` : raw;
      }
      if (candidate) return sources[candidate]?.get(raw) || raw;
      const enumField =
        spec?.fields.find((f) => f.name === fieldName)?.kind === "enum";
      return enumField ? labels[raw] || raw : raw;
    }
    if (Array.isArray(raw))
      return (
        raw
          .map((v) =>
            typeof v === "object" && v !== null && "id" in v
              ? sources[
                  spec?.fields.find((f) => f.name === fieldName)?.candidate ||
                    "combatants"
                ]?.get(String(v.id)) || String(v.id)
              : value(v, fieldName),
          )
          .join("、") || "无"
      );
    if (typeof raw === "object") {
      const obj = raw as Record<string, unknown>;
      if (fieldName === "from" || fieldName === "to") {
        const holder = holderSchema.safeParse(obj);
        if (holder.success)
          return (
            sources.holders?.get(holderValue(holder.data)) ||
            `${HOLDER_LABEL[holder.data.kind]} · ${holder.data.id}`
          );
      }
      if (fieldName === "audience") {
        if (obj.kind === "public") return "所有人";
        if (obj.kind === "keeper") return "仅主持";
        if (obj.kind === "investigators")
          return `指定调查员：${(Array.isArray(obj.investigator_ids) ? obj.investigator_ids : []).map((id) => investigators.get(String(id)) || String(id)).join("、")}`;
      }
      if (fieldName === "speaker" || fieldName === "target")
        return obj.kind === "keeper"
          ? "守秘人"
          : (obj.kind === "investigator"
              ? investigators
              : obj.kind === "npc"
                ? npcs
                : undefined
            )?.get(String(obj.id || "")) ||
              String(obj.text || obj.id || obj.kind || "未指定");
      if (fieldName === "pending_action")
        return (
          String(obj.note || "") ||
          sources.scenes?.get(String(obj.destination_scene_id || "")) ||
          String(obj.destination_scene_id || obj.target || obj.kind || "未指定")
        );
      return JSON.stringify(obj);
    }
    return String(raw);
  };
  return (
    <li className="draft-command-preview">
      <h4>
        <span aria-hidden="true">{index + 1}</span>
        {spec?.label || `未识别的操作：${command.kind}`}
      </h4>
      <dl className="draft-command-facts">
        {Object.entries(command.payload).map(([key, raw]) => (
          <div key={key}>
            <dt>
              {spec?.fields.find((f) => f.name === key)?.label ||
                (
                  {
                    pending_action: "尚未执行",
                    disclosed: "已告知",
                    thread: "交互线程",
                    from: "来源",
                    to: "去向",
                  } as Record<string, string>
                )[key] ||
                `参数：${key}`}
            </dt>
            <dd>{value(raw, key)}</dd>
          </div>
        ))}
      </dl>
      {command.kind === "advance_time" && (
        <p className="draft-command-caution">
          未指定活动类型时按等待计时；这里只推进时间，不执行移动、检定或战斗。
        </p>
      )}
      {command.kind === "combat_action" && (
        <p className="draft-command-caution">
          批准不代表命中；涉及玩家确认或掷骰时，仍会等待玩家响应。
        </p>
      )}
      {!spec && (
        <p className="draft-command-caution">
          尚无对应主持操作，请先核对原始参数；批准不能绕过服务端校验。
        </p>
      )}
      <details className="draft-command-raw">
        <summary>查看原始参数</summary>
        <pre>{JSON.stringify(command, null, 2)}</pre>
      </details>
    </li>
  );
}
