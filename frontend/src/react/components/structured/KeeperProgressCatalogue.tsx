import { useState } from "react";
import type { KeeperProgress } from "../../../protocol/keeper-progress";
import type { FieldValues } from "../../../protocol/keeper-commands";

const INTENT: Record<string, string> = {
  search: "搜查",
  examine: "检查",
  read: "阅读",
  take: "取走",
  talk: "交谈",
  use: "使用物品",
};
const CLOCK: Record<string, string> = {
  clue_clarity: "线索清晰度",
  monster_manifestation: "异象进展",
  human_pressure: "人际压力",
};

export function KeeperProgressCatalogue({
  state,
  sceneId,
  blocked,
  onPrepare,
}: {
  state: KeeperProgress;
  sceneId: string;
  blocked: string | null;
  onPrepare: (
    kind: "grant_clue" | "use_item" | "request_check",
    values: FieldValues,
  ) => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const clues = state.clues.filter(
    (c) =>
      (filter === "all" || (filter === "found") === c.discovered) &&
      `${c.id} ${c.text} ${c.granted_item}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <section
      className="keeper-progress-catalogue"
      data-testid="keeper-progress-catalogue"
      aria-label="模组线索与案件时钟"
    >
      <details className="keeper-progress-clocks" open>
        <summary>案件时钟 · 仅主持可见</summary>
        {state.clocks.length === 0 ? (
          <p>模组未声明案件时钟。</p>
        ) : (
          <div className="keeper-progress-clock-grid">
            {state.clocks.map((c) => (
              <article key={c.id} data-testid={`keeper-clock-${c.id}`}>
                <h5>{CLOCK[c.title] || c.title}</h5>
                <p className="keeper-clock-number">
                  {c.value ?? "未记录"} / {c.max ?? "未声明上限"}
                </p>
                {c.value !== null && c.max !== null && c.max > 0 && (
                  <progress
                    aria-label={`${CLOCK[c.title] || c.title}进度`}
                    value={c.value}
                    max={c.max}
                  />
                )}
                {c.level && <p>{c.level}</p>}
                <details>
                  <summary>推进依据</summary>
                  {c.next_level && <p>下一级：{c.next_level}</p>}
                  {c.advance_when.map((s, i) => (
                    <p key={i}>{s}</p>
                  ))}
                </details>
              </article>
            ))}
          </div>
        )}
      </details>
      <details className="keeper-authored-clues">
        <summary>模组线索目录（{state.clues.length} 条）</summary>
        <p className="structured-card-note">
          仅主持可见。发放信息不等于取得实物；作者效果和检定须显式结算。下方按钮只准备表单，不执行行动。
        </p>
        <div className="keeper-catalogue-filters">
          <label>
            搜索线索
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="名称、正文或模组编号"
            />
          </label>
          <label>
            查看范围
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">全部</option>
              <option value="found">已记录</option>
              <option value="hidden">未记录</option>
            </select>
          </label>
        </div>
        <p role="status">显示 {clues.length} 条</p>
        {clues.map((c) => (
          <details
            key={c.id}
            className="keeper-authored-clue"
            data-clue-id={c.id}
          >
            <summary>
              {c.granted_item || c.text.slice(0, 40) || c.id} ·{" "}
              {c.discovered ? "已记录" : "未记录"}
            </summary>
            <p>{c.text}</p>
            <p className="structured-card-note">
              模组编号：<code>{c.id}</code>
            </p>
            {c.granted_item && (
              <p>
                实物：{c.granted_item} ·{" "}
                {c.item_id
                  ? `已经取得（持有人 ${c.holder_id}）`
                  : "尚未登记取得"}
              </p>
            )}
            <div className="keeper-catalogue-actions">
              <button
                type="button"
                className="btn-ghost"
                disabled={!!blocked}
                title={blocked || undefined}
                onClick={() => onPrepare("grant_clue", { clue_id: c.id })}
              >
                准备发放信息
              </button>
            </div>
            {c.rules.map((r) => {
              const unmet = r.conditions.filter((p) => !p.satisfied);
              const reason =
                blocked ||
                (c.related_scenes.length && !c.related_scenes.includes(sceneId)
                  ? "不属于当前场景。"
                  : null) ||
                (unmet.length ? "作者前置条件尚未满足。" : null);
              return (
                <article key={r.index} className="keeper-discovery-rule">
                  <h6>
                    规则 {r.index} · {INTENT[r.intent] || r.intent}
                  </h6>
                  {r.approach && <p>{r.approach}</p>}
                  {r.requires_success && (
                    <p>
                      需要已成功检定：{r.skill} · {r.difficulty}；目标须为{" "}
                      <code>{c.id}</code>。
                    </p>
                  )}
                  {r.sanity_note && (
                    <p>
                      作者 SAN 提示：{r.sanity_note}（另行申请并结算 SAN
                      检定，不自动扣减）。
                    </p>
                  )}
                  {r.conditions.length > 0 && (
                    <ul>
                      {r.conditions.map((p) => (
                        <li key={p.flag_id}>
                          {p.flag_id}：{p.current_text || "未记录"} → 要求{" "}
                          {p.expected_text} ·{" "}
                          {p.satisfied ? "已满足" : "未满足"}
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="keeper-catalogue-actions">
                    {r.requires_success && (
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={!!reason}
                        title={reason || undefined}
                        onClick={() =>
                          onPrepare("request_check", {
                            skill: r.skill,
                            difficulty: r.difficulty,
                            attempt: (r.approach || c.text).slice(0, 200),
                            target_kind: "scene_object",
                            target_id: c.id,
                            visibility: "public",
                          })
                        }
                      >
                        准备发现检定
                      </button>
                    )}
                    {r.intent === "use" ? (
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={!!reason}
                        title={reason || undefined}
                        onClick={() =>
                          onPrepare("use_item", {
                            effect_clue_id: c.id,
                            effect_rule_index: String(r.index),
                            quantity: "1",
                            operation:
                              r.approach.slice(0, 60) || "按作者规则使用",
                          })
                        }
                      >
                        准备使用效果
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="btn-ghost"
                          disabled={!!reason}
                          title={reason || undefined}
                          onClick={() =>
                            onPrepare("grant_clue", {
                              clue_id: c.id,
                              discovery_rule_index: String(r.index),
                            })
                          }
                        >
                          准备结算发现
                        </button>
                        {c.granted_item && (
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={!!reason || !!c.item_id}
                            title={
                              reason ||
                              (c.item_id
                                ? "已有实物，请在物品转移中选择当前持有人。"
                                : undefined)
                            }
                            onClick={() =>
                              onPrepare("grant_clue", {
                                clue_id: c.id,
                                discovery_rule_index: String(r.index),
                                acquire_item: true,
                              })
                            }
                          >
                            准备取得实物
                          </button>
                        )}
                      </>
                    )}
                  </div>
                  {reason && <p className="structured-card-note">{reason}</p>}
                </article>
              );
            })}
          </details>
        ))}
      </details>
    </section>
  );
}
