import { useAppStore } from "../../../state/app-store";
import {
  useInvestigatorPanelStore,
  useWorldPrefs,
} from "../../../state/investigator-panel-store";
import { CollapsibleCard } from "./CollapsibleCard";

function percentage(value?: number, maximum?: number) {
  if (!Number.isFinite(value) || !Number.isFinite(maximum) || !maximum)
    return 0;
  return Math.max(0, Math.min(100, (Number(value) / Number(maximum)) * 100));
}

/** 低 HP/SAN 的中性文本提醒：只陈述数值偏低，不从数值猜昏迷/疯狂等状态。 */
function lowWarning(ratio: number): string | null {
  return ratio > 0 && ratio <= 30 ? "偏低" : null;
}

// 服务端公开 conditions 的本地化标签；只展示已下发的事实，不做推测。
const CONDITION_LABELS: Record<string, string> = {
  major_wound: "重伤",
  prone: "倒地",
  unconscious: "昏迷",
  dying: "濒死",
  dead: "死亡",
};

export function CharacterStatusCard() {
  const character = useAppStore((state) => state.character);
  const prefs = useWorldPrefs();
  const toggleCard = useInvestigatorPanelStore((state) => state.toggleCard);
  const setAttributesOpen = useInvestigatorPanelStore(
    (state) => state.setAttributesOpen,
  );

  const hpRatio = percentage(character?.hp, character?.max_hp);
  const sanRatio = percentage(character?.san, character?.max_san);
  const hpWarn = lowWarning(hpRatio);
  const sanWarn = lowWarning(sanRatio);
  const attributes = Object.entries(character?.attributes || {});
  const avatarSrc =
    character?.avatar?.asset_data_uri || character?.avatar?.asset_url || "";

  const collapsedSummary = (
    <>
      {character
        ? `HP ${character.hp}/${character.max_hp} · SAN ${character.san}/${character.max_san}`
        : "HP --/-- · SAN --/--"}
      {hpWarn && <em className="inv-warn">HP {hpWarn}</em>}
      {sanWarn && <em className="inv-warn">SAN {sanWarn}</em>}
    </>
  );

  return (
    <CollapsibleCard
      cardId="status"
      title="人物状态"
      emblem="❖"
      collapsed={prefs.collapsed.status}
      onToggle={() => toggleCard("status")}
      summary={collapsedSummary}
    >
      <div className="inv-status-identity">
        {avatarSrc ? (
          <img
            className="inv-avatar"
            src={avatarSrc}
            alt={character?.name || "调查员头像"}
          />
        ) : null}
        <div className="inv-status-names">
          <h3 id="char-name">{character?.name || "调查员"}</h3>
          <p className="sub" id="char-occupation">
            {character?.occupation || "档案载入中…"}
          </p>
        </div>
      </div>

      <div className="stat-row">
        <span>HP</span>
        <span id="hp-bar">
          {character ? `${character.hp} / ${character.max_hp}` : "-- / --"}
          {hpWarn && <em className="inv-warn">（{hpWarn}）</em>}
        </span>
      </div>
      <div className="stat-bar">
        <div
          id="hp-fill"
          className="stat-bar-fill hp"
          style={{ width: `${hpRatio}%` }}
        />
      </div>
      <div className="stat-row">
        <span>SAN</span>
        <span id="san-bar">
          {character ? `${character.san} / ${character.max_san}` : "-- / --"}
          {sanWarn && <em className="inv-warn">（{sanWarn}）</em>}
        </span>
      </div>
      <div className="stat-bar">
        <div
          id="san-fill"
          className="stat-bar-fill san"
          style={{ width: `${sanRatio}%` }}
        />
      </div>

      {(character?.conditions || []).length > 0 && (
        <div className="inv-conditions" aria-label="人物状态">
          {(character?.conditions || []).map((condition) => (
            <span className="inv-condition-tag" key={condition}>
              {CONDITION_LABELS[condition] || condition}
            </span>
          ))}
        </div>
      )}

      {attributes.length > 0 && (
        <div className="inv-attributes">
          <button
            type="button"
            className="inv-attributes-toggle"
            aria-expanded={prefs.attributesOpen}
            aria-controls="inv-attr-list"
            onClick={() => setAttributesOpen(!prefs.attributesOpen)}
          >
            <span className="inv-attr-caret" aria-hidden="true">
              ▾
            </span>{" "}
            详细属性
            <span className="inv-attributes-count">{attributes.length}</span>
          </button>
          <div
            className={`inv-collapse inv-attr-wrap${prefs.attributesOpen ? "" : " closed"}`}
            aria-hidden={!prefs.attributesOpen}
          >
            <div className="inv-collapse-clip">
              <div id="inv-attr-list">
                {attributes.map(([key, value]) => (
                  <div className="stat-row" key={key}>
                    <span>{key}</span>
                    <span>{String(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </CollapsibleCard>
  );
}
