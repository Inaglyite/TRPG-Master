import type { RulingState } from "../../../protocol/rulings";

const TYPE_LABELS: Record<string, string> = {
  good: "良好",
  neutral: "中立",
  bad: "不利",
  secret: "隐藏",
};

/** Keeper-only author notes, not a quest list or a shortcut to fulfilling flags. */
export function KeeperEndingCatalogue({
  state,
  blocked,
  onPrepare,
}: {
  state: RulingState;
  blocked: string | null;
  onPrepare: (id: string) => void;
}) {
  const catalogue = state.ending_catalog;
  return (
    <section
      className="keeper-ending-catalogue"
      data-testid="keeper-ending-catalogue"
      aria-label="结局条件"
    >
      <header>
        <h4>结局条件</h4>
        <span>仅主持可见</span>
      </header>
      <p className="keeper-note">
        这里只核对模组条件并准备表单，不自动结束游戏，也不会补齐缺失事实。
      </p>
      {catalogue === undefined ? (
        <>
          <p className="keeper-note">
            服务端只提供已满足列表，完整条件尚未提供。
          </p>
          {state.eligible_endings.map((e) => (
            <article key={e.id} className="keeper-ending-slip">
              <h5>{e.title || e.id}</h5>
              <p>前置条件已满足；实际结算仍由服务端复核。</p>
              <button
                type="button"
                className="btn-ghost"
                disabled={!!blocked}
                onClick={() => onPrepare(e.id)}
              >
                准备结算
              </button>
            </article>
          ))}
        </>
      ) : catalogue.length === 0 ? (
        <p className="keeper-note">
          模组尚未声明配置结局，可查看模组手册后使用主持结算表单。
        </p>
      ) : (
        catalogue.map((e) => {
          const reason =
            blocked ||
            e.blocked_reason ||
            (!e.eligible || !e.can_prepare ? "当前不能准备结算。" : null);
          return (
            <article
              key={e.id}
              className="keeper-ending-slip"
              data-ending-id={e.id}
            >
              <div className="keeper-ending-slip-heading">
                <h5>{e.title || e.id}</h5>
                <span
                  className={
                    e.eligible
                      ? "ending-condition-stamp is-satisfied"
                      : "ending-condition-stamp"
                  }
                >
                  {e.eligible ? "条件已齐" : "条件未齐"}
                </span>
              </div>
              <p className="ending-kind-note">
                {TYPE_LABELS[e.ending_type]}结局 · <code>{e.id}</code>
              </p>
              <details className="ending-condition-details">
                <summary>条件清单（{e.conditions.length} 项）</summary>
                {e.conditions.length === 0 ? (
                  <p>作者未声明状态前置条件；是否收尾仍需主持结合情境判断。</p>
                ) : (
                  <ul>
                    {e.conditions.map((c) => (
                      <li key={c.flag_id}>
                        <strong>{c.flag_id}</strong>
                        <dl>
                          <div>
                            <dt>期望值</dt>
                            <dd>{c.expected_text}</dd>
                          </div>
                          <div>
                            <dt>当前值</dt>
                            <dd>{c.recorded ? c.current_text : "未记录"}</dd>
                          </div>
                        </dl>
                        <span
                          className={
                            c.satisfied
                              ? "ending-condition-status is-satisfied"
                              : "ending-condition-status"
                          }
                        >
                          {c.satisfied ? "已满足" : "未满足"}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
              {(e.trigger || e.description) && (
                <details className="ending-author-details">
                  <summary>作者说明</summary>
                  {e.trigger && (
                    <p>
                      <strong>收尾情境：</strong>
                      {e.trigger}
                    </p>
                  )}
                  {e.description && <p>{e.description}</p>}
                </details>
              )}
              <div className="ending-prepare-row">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={!!reason}
                  title={reason || undefined}
                  onClick={() => {
                    if (!reason) onPrepare(e.id);
                  }}
                >
                  准备结算
                </button>
              </div>
              {reason && <p className="ending-blocked-note">{reason}</p>}
            </article>
          );
        })
      )}
    </section>
  );
}
