import type { CharacterOption } from "../../state/start-store";

const attributes: Record<string, string> = {
  STR: "力量",
  CON: "体质",
  SIZ: "体型",
  DEX: "敏捷",
  APP: "外貌",
  INT: "智力",
  POW: "意志",
  EDU: "教育",
};
const skills: Record<string, string> = {
  spot_hidden: "侦查",
  listen: "聆听",
  library_use: "图书馆使用",
  psychology: "心理学",
  fast_talk: "话术",
  persuade: "说服",
  charm: "魅惑",
  intimidate: "恐吓",
  fighting_brawl: "格斗",
  firearms_handgun: "手枪",
  firearms_rifle: "步枪/霰弹枪",
  dodge: "闪避",
  stealth: "潜行",
  first_aid: "急救",
  medicine: "医学",
  occult: "神秘学",
  history: "历史",
  law: "法律",
  navigate: "导航",
  track: "追踪",
  language_own: "母语",
  credit_rating: "信用评级",
  cthulhu_mythos: "克苏鲁神话",
};

function inventoryLabel(item: unknown) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    const value = item as Record<string, unknown>;
    const label = value.label || value.name || value.id;
    if (typeof label === "string")
      return `${label}${typeof value.quantity === "number" ? ` × ${value.quantity}` : ""}`;
  }
  return String(item);
}

/**
 * 调查员档案卡（羊皮纸 dossier）：本地开始页与云端单人角色选择共用。
 * 按角色 id 作 key 重挂载可播放 dossier-in 入场动画。
 */
export function CharacterDossier({
  character,
}: {
  character: CharacterOption;
}) {
  const derived = character.derived || {};
  const backstory = character.backstory || {};
  const story = [
    ["外貌", character.description || backstory.description],
    ["经历", backstory.background],
    ["信念", backstory.beliefs],
    ["特质", backstory.traits],
  ].filter(([, value]) => typeof value === "string" && value);
  return (
    <article className="character-dossier dossier-anim">
      <header className="character-detail-header">
        <div className="character-detail-identity">
          <h3>{character.name}</h3>
          <p>
            {[
              character.occupation || "调查员",
              character.age ? `${character.age} 岁` : "",
              character.era || "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <span className="character-source-badge">{character.source_label}</span>
      </header>
      <div className="character-vitals-grid">
        {[
          ["HP", `${character.hp}/${character.max_hp}`],
          ["SAN", `${character.san}/${character.max_san}`],
          ["MP", derived.MP],
          ["幸运", derived.LUCK],
          ["移动", derived.MOV],
          ["伤害加值", derived.DB],
          ["体格", derived.BUILD],
          ["信用", character.credit_rating],
        ]
          .filter(([, value]) => value !== undefined && value !== "")
          .map(([label, value]) => (
            <div className="character-vital" key={String(label)}>
              <span>{String(label)}</span>
              <strong>{String(value)}</strong>
            </div>
          ))}
      </div>
      {character.attributes && (
        <section className="character-detail-section">
          <h4>基础属性</h4>
          <div className="character-attribute-grid">
            {Object.entries(attributes)
              .filter(([id]) => typeof character.attributes?.[id] === "number")
              .map(([id, label]) => (
                <div className="character-attribute" key={id}>
                  <span>
                    {label} {id}
                  </span>
                  <strong>{character.attributes?.[id]}</strong>
                </div>
              ))}
          </div>
        </section>
      )}
      {Boolean(character.top_skills?.length) && (
        <section className="character-detail-section">
          <h4>擅长技能</h4>
          <div className="character-skill-list">
            {character.top_skills?.map((skill) => (
              <div className="character-skill" key={skill.id}>
                <span>{skills[skill.id] || skill.id.replaceAll("_", " ")}</span>
                <strong>{skill.value}</strong>
              </div>
            ))}
          </div>
        </section>
      )}
      {story.length > 0 && (
        <section className="character-detail-section">
          <h4>人物档案</h4>
          <div className="character-story">
            {story.map(([label, value]) => (
              <p key={String(label)}>
                <strong>{String(label)}：</strong>
                {String(value)}
              </p>
            ))}
          </div>
        </section>
      )}
      {Boolean(character.inventory?.length) && (
        <section className="character-detail-section">
          <h4>随身物品</h4>
          <ul className="character-inventory">
            {character.inventory?.map((item, index) => (
              <li key={index}>{inventoryLabel(item)}</li>
            ))}
          </ul>
        </section>
      )}
      {Boolean(character.completed_modules || character.reputation) && (
        <section className="character-detail-section">
          <h4>调查履历</h4>
          <div className="character-career-note">
            完成案件 {character.completed_modules} · 声望 {character.reputation}
          </div>
        </section>
      )}
    </article>
  );
}
