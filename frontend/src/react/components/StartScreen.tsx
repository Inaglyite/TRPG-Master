import { useEffect } from "react";

import { continueGame, startGame, switchModule } from "../../start";
import { useAppStore } from "../../state/app-store";
import { useStartStore, type CharacterOption } from "../../state/start-store";
import { ModelSettingsTrigger } from "./ModelSettingsPanel";
import { ModuleImporter } from "./ModuleImporter";

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

async function startCommand(
  command: "startGame" | "continueGame" | "switchModule",
  value?: string,
) {
  if (command === "switchModule") switchModule(value || "");
  else if (command === "startGame") startGame();
  else continueGame();
}
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
function CharacterDetail({ character }: { character: CharacterOption }) {
  const derived = character.derived || {};
  const backstory = character.backstory || {};
  const story = [
    ["外貌", character.description || backstory.description],
    ["经历", backstory.background],
    ["信念", backstory.beliefs],
    ["特质", backstory.traits],
  ].filter(([, value]) => typeof value === "string" && value);
  return (
    <article className="character-dossier">
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

export function StartScreen() {
  const state = useStartStore();
  const title = useAppStore((value) => value.title);
  const subtitle = useAppStore((value) => value.subtitle);
  const description = useAppStore((value) => value.description);
  const startButtonText = useAppStore((value) => value.startButtonText);
  const characters = state.characterGroups.flatMap(
    (group) => group.characters || [],
  );
  const selected =
    characters.find(
      (character) => character.id === state.selectedCharacterId,
    ) || characters[0];
  useEffect(() => {
    if (selected && !state.selectedCharacterRef)
      useStartStore.setState({
        selectedCharacterId: selected.id,
        selectedCharacterRef: selected.ref,
      });
  }, [selected?.id]);
  useEffect(() => {
    if (state.view !== "characters") return;
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !state.gameStarting)
        useStartStore.setState({ view: "menu" });
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, [state.view, state.gameStarting]);
  if (state.gameStarted) return <div id="start-overlay" className="hidden" />;
  return (
    <div id="start-overlay">
      <div id="start-box">
        {state.view === "menu" ? (
          <section
            id="start-menu-view"
            className="start-view"
            aria-label="主菜单"
          >
            <div className="start-brand">
              <div id="start-title" className="fx-glow">
                {title}
              </div>
              <div id="start-subtitle">{subtitle}</div>
              {description && <div id="start-description">{description}</div>}
            </div>
            <div id="module-selector">
              <label htmlFor="module-select">当前模组</label>
              <div className="module-select-row">
                <select
                  id="module-select"
                  value={state.activeModule}
                  disabled={state.moduleSwitchPending}
                  onChange={(event) =>
                    void startCommand("switchModule", event.target.value)
                  }
                >
                  {state.modules.map((module) => (
                    <option value={module.id} key={module.id}>
                      {module.title}
                    </option>
                  ))}
                </select>
                <ModuleImporter />
              </div>
            </div>
            <nav className="start-menu-actions">
              <button
                id="btn-start"
                className="start-art-button"
                disabled={
                  state.gameStarting ||
                  state.moduleSwitchPending ||
                  !state.charactersReady ||
                  !characters.length
                }
                onClick={() => useStartStore.setState({ view: "characters" })}
              >
                <span className="start-art-label">
                  {startButtonText || "开始新游戏"}
                </span>
              </button>
              <button
                id="btn-continue"
                className="start-art-button"
                disabled={!state.hasSaves || state.gameStarting}
                onClick={() => void startCommand("continueGame")}
              >
                <span className="start-art-label">从存档开始</span>
              </button>
              <ModelSettingsTrigger />
              {/\bElectron\//.test(navigator.userAgent) && (
                <button
                  id="btn-exit"
                  className="start-menu-button start-menu-exit"
                  onClick={() => window.close()}
                >
                  <span>⏻</span>
                  <span>退出游戏</span>
                </button>
              )}
            </nav>
            <div id="start-hint">{state.hint}</div>
          </section>
        ) : (
          <section id="character-select-view" className="start-view">
            <header className="character-select-header">
              <button
                id="btn-character-back"
                className="character-back-button"
                disabled={state.gameStarting}
                onClick={() => useStartStore.setState({ view: "menu" })}
              >
                ← 返回主菜单
              </button>
              <div className="character-select-heading">
                <h2>选择调查员</h2>
                <div id="character-module-name">{state.activeModuleTitle}</div>
              </div>
            </header>
            <div className="investigator-layout">
              <section id="character-selector" className="character-roster">
                <div className="character-roster-title">可用调查员</div>
                <div id="character-choice-list">
                  {state.characterGroups.map((group) => (
                    <section className="character-group" key={group.id}>
                      <div className="character-group-title">
                        {group.id === "module"
                          ? `${state.activeModuleTitle} 特色调查员`
                          : group.title}
                      </div>
                      <div className="character-card-row">
                        {group.characters.map((character) => (
                          <button
                            className={`character-card${selected?.id === character.id ? " selected" : ""}`}
                            aria-pressed={selected?.id === character.id}
                            key={character.id}
                            onClick={() =>
                              useStartStore.setState({
                                selectedCharacterId: character.id,
                                selectedCharacterRef: character.ref,
                              })
                            }
                          >
                            <span className="character-card-name">
                              {character.name}
                            </span>
                            <span className="character-card-source">
                              {character.source_label}
                            </span>
                            <span className="character-card-meta">
                              {character.occupation || "调查员"}
                            </span>
                            <span className="character-card-vitals">
                              HP {character.hp}/{character.max_hp} · SAN{" "}
                              {character.san}/{character.max_san}
                            </span>
                          </button>
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
              </section>
              <aside id="character-detail">
                {selected ? (
                  <CharacterDetail character={selected} />
                ) : (
                  <div className="character-detail-empty">
                    {state.charactersReady
                      ? "当前模组没有可用的调查员档案"
                      : "正在读取调查员档案…"}
                  </div>
                )}
              </aside>
            </div>
            <footer className="character-select-footer">
              <span id="character-selected-summary">
                {selected
                  ? `${selected.name} · ${selected.occupation || "调查员"} · ${selected.source_label}`
                  : "未选择调查员"}
              </span>
              <button
                id="btn-character-confirm"
                disabled={state.gameStarting || !state.selectedCharacterRef}
                onClick={() => void startCommand("startGame")}
              >
                {state.gameStarting ? "守秘人正在布景…" : "以此调查员开始"}
              </button>
            </footer>
          </section>
        )}
      </div>
    </div>
  );
}
