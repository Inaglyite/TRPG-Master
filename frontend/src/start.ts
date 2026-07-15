/** 开局主菜单、调查员选择与 GM 首回合状态。 */

import {
  startOverlay,
  startMenuView,
  characterSelectView,
  btnStart,
  btnContinue,
  btnExit,
  btnNew,
  btnCharacterBack,
  btnCharacterConfirm,
  characterChoiceList,
  characterSelectedSummary,
  characterDetail,
  characterModuleName,
} from "./dom";
import { safeSend } from "./ws";
import { showGmThinking } from "./renderer";
import { openSavePanel, renderSavePanel } from "./panels";
import { enableInput } from "./options";

export let gameStarted = false;
export let gameStarting = false;

type CharacterRef = {
  source: string;
  id?: string;
  module?: string;
  file?: string;
  path?: string;
};

type CharacterOption = {
  ref: CharacterRef;
  id: string;
  name: string;
  occupation: string;
  age?: number | null;
  era?: string;
  source_label: string;
  hp: number;
  max_hp: number;
  san: number;
  max_san: number;
  reputation: number;
  completed_modules: number;
  credit_rating?: number;
  attributes?: Record<string, number>;
  derived?: Record<string, number | string>;
  inventory?: unknown[];
  backstory?: Record<string, unknown>;
  top_skills?: { id: string; value: number }[];
  description?: string;
};

type CharacterGroup = {
  id: string;
  title: string;
  characters: CharacterOption[];
};

type ModuleOption = {
  id: string;
  title: string;
};

const ATTRIBUTE_LABELS: Record<string, string> = {
  STR: "力量",
  CON: "体质",
  SIZ: "体型",
  DEX: "敏捷",
  APP: "外貌",
  INT: "智力",
  POW: "意志",
  EDU: "教育",
};

const SKILL_LABELS: Record<string, string> = {
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

let selectedCharacterRef: CharacterRef | null = null;
let selectedCharacterId = "";
let characterGroups: CharacterGroup[] = [];
let charactersReady = false;
let hasSaves = false;
let moduleSwitchPending = false;
let activeModule = "";
let activeModuleTitle = "当前模组";
let saveHintText = "正在读取存档…";
let startRetryTimer: number | null = null;
let startRetryAttempt = 0;

const START_RETRY_DELAYS = [400, 700, 1000, 1500, 2200, 3000];

function clearStartRetry() {
  if (startRetryTimer !== null) {
    window.clearTimeout(startRetryTimer);
    startRetryTimer = null;
  }
}

function sendStartRequest() {
  if (!gameStarting || !selectedCharacterRef) return;
  safeSend(JSON.stringify({ type: "start", character_ref: selectedCharacterRef }));
}

function createElement<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = "",
  text = "",
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function allCharacters(): CharacterOption[] {
  return characterGroups.flatMap((group) => group.characters || []);
}

function isCharacterViewOpen(): boolean {
  return !characterSelectView.classList.contains("hidden");
}

function showStartView(view: "menu" | "characters") {
  const showCharacters = view === "characters";
  startMenuView.classList.toggle("hidden", showCharacters);
  characterSelectView.classList.toggle("hidden", !showCharacters);
  if (showCharacters) {
    requestAnimationFrame(() => {
      const selected = characterChoiceList.querySelector<HTMLButtonElement>(
        ".character-card.selected",
      );
      (selected || btnCharacterBack).focus();
    });
  } else {
    requestAnimationFrame(() => btnStart.focus());
  }
}

export function getGameStarted(): boolean {
  return gameStarted;
}

export function getGameStarting(): boolean {
  return gameStarting;
}

export function onGmTurnStart() {
  if (!gameStarted) {
    clearStartRetry();
    startRetryAttempt = 0;
    gameStarting = false;
    gameStarted = true;
    startOverlay.classList.add("hidden");
  }
  enableInput(false);
  showGmThinking();
}

export function resetStartButton() {
  clearStartRetry();
  startRetryAttempt = 0;
  gameStarting = false;
  moduleSwitchPending = false;
  const moduleSelect = document.getElementById("module-select") as HTMLSelectElement | null;
  if (moduleSelect) {
    moduleSelect.disabled = false;
    moduleSelect.value = activeModule;
  }
  document.getElementById("start-hint")!.textContent = saveHintText;
  btnStart.disabled = !charactersReady || allCharacters().length === 0;
  btnContinue.disabled = !hasSaves;
  btnCharacterBack.disabled = false;
  btnCharacterConfirm.disabled = selectedCharacterRef === null;
  btnCharacterConfirm.textContent = "以此调查员开始";
}

function openCharacterSelection() {
  if (gameStarting || moduleSwitchPending || !charactersReady) return;
  characterModuleName.textContent = activeModuleTitle;
  showStartView("characters");
}

export function startGame() {
  if (gameStarting || !selectedCharacterRef) return;
  clearStartRetry();
  startRetryAttempt = 0;
  gameStarting = true;
  btnStart.disabled = true;
  btnContinue.disabled = true;
  btnCharacterBack.disabled = true;
  btnCharacterConfirm.disabled = true;
  btnCharacterConfirm.textContent = "守秘人正在布景…";
  sendStartRequest();
}

export function onStartTurnRejected(message: string, retryable: boolean): boolean {
  if (!gameStarting) return false;
  clearStartRetry();
  const hint = document.getElementById("start-hint")!;
  if (retryable && startRetryAttempt < START_RETRY_DELAYS.length) {
    const delay = START_RETRY_DELAYS[startRetryAttempt++];
    hint.textContent = `${message} 正在自动重试……`;
    btnCharacterConfirm.textContent = "正在结束上一局…";
    startRetryTimer = window.setTimeout(() => {
      startRetryTimer = null;
      sendStartRequest();
    }, delay);
    return true;
  }
  resetStartButton();
  hint.textContent = message;
  return true;
}

export function continueGame() {
  if (!hasSaves) return;
  openSavePanel("load");
}

export function onSaveAvailable(data: any) {
  if (!data.has_save) return;
  hasSaves = true;
  btnContinue.disabled = false;
}

export function onSaveList(data: any) {
  const saves = Array.isArray(data.saves) ? data.saves : [];
  const hint = document.getElementById("start-hint")!;
  hasSaves = saves.length > 0;
  btnContinue.disabled = !hasSaves;

  saveHintText = "";
  hint.textContent = saveHintText;
  renderSavePanel(saves);
}

export function populateModuleList(modules: ModuleOption[], active: string) {
  const select = document.getElementById("module-select") as HTMLSelectElement;
  if (!select) return;

  const changed = Boolean(activeModule && activeModule !== active);
  if (changed) {
    selectedCharacterRef = null;
    selectedCharacterId = "";
    characterGroups = [];
    charactersReady = false;
    hasSaves = false;
    renderCharacterList([]);
  }

  activeModule = active;
  activeModuleTitle = modules.find((module) => module.id === active)?.title || active;
  characterModuleName.textContent = activeModuleTitle;
  moduleSwitchPending = false;
  select.disabled = false;
  select.replaceChildren();

  for (const module of modules) {
    const option = document.createElement("option");
    option.value = module.id;
    option.textContent = module.title;
    option.selected = module.id === active;
    select.appendChild(option);
  }

  btnStart.disabled = !charactersReady || allCharacters().length === 0;
  select.onchange = () => {
    const chosen = select.value;
    if (chosen === activeModule || moduleSwitchPending) return;
    moduleSwitchPending = true;
    btnStart.disabled = true;
    btnContinue.disabled = true;
    select.disabled = true;
    document.getElementById("start-hint")!.textContent = "正在切换模组…";
    safeSend(JSON.stringify({ type: "switch_module", module: chosen }));
  };
}

export function populateCharacterList(groups: CharacterGroup[]) {
  characterGroups = groups || [];
  charactersReady = true;
  renderCharacterList(characterGroups);
  btnStart.disabled = allCharacters().length === 0 || moduleSwitchPending;
}

function renderCharacterList(groups: CharacterGroup[]) {
  characterChoiceList.replaceChildren();
  const characters = groups.flatMap((group) => group.characters || []);
  if (characters.length === 0) {
    selectedCharacterRef = null;
    selectedCharacterId = "";
    characterChoiceList.appendChild(
      createElement("div", "character-empty", charactersReady ? "暂无可用调查员" : "正在读取调查员…"),
    );
    characterSelectedSummary.textContent = charactersReady ? "未选择调查员" : "等待角色列表…";
    btnCharacterConfirm.disabled = true;
    characterDetail.replaceChildren(
      createElement(
        "div",
        "character-detail-empty",
        charactersReady ? "当前模组没有可用的调查员档案" : "正在读取调查员档案…",
      ),
    );
    return;
  }

  const selected = characters.find((character) => character.id === selectedCharacterId)
    || characters[0];

  for (const group of groups) {
    if (!group.characters?.length) continue;
    const section = createElement("section", "character-group");
    const groupTitle = group.id === "module"
      ? `${activeModuleTitle} 特色调查员`
      : group.title;
    section.appendChild(createElement("div", "character-group-title", groupTitle));
    const row = createElement("div", "character-card-row");

    for (const character of group.characters) {
      const card = createElement("button", "character-card") as HTMLButtonElement;
      card.type = "button";
      card.dataset.characterId = character.id;
      card.setAttribute("aria-controls", "character-detail");

      card.appendChild(createElement("span", "character-card-name", character.name));
      card.appendChild(createElement(
        "span",
        "character-card-source",
        character.source_label,
      ));
      card.appendChild(createElement(
        "span",
        "character-card-meta",
        character.occupation || "调查员",
      ));
      card.appendChild(createElement(
        "span",
        "character-card-vitals",
        `HP ${character.hp}/${character.max_hp} · SAN ${character.san}/${character.max_san}`,
      ));
      card.onclick = () => selectCharacter(character);
      row.appendChild(card);
    }

    section.appendChild(row);
    characterChoiceList.appendChild(section);
  }

  selectCharacter(selected);
}

function selectCharacter(character: CharacterOption) {
  selectedCharacterRef = character.ref;
  selectedCharacterId = character.id;
  characterChoiceList.querySelectorAll<HTMLButtonElement>(".character-card").forEach((card) => {
    const selected = card.dataset.characterId === character.id;
    card.classList.toggle("selected", selected);
    card.setAttribute("aria-pressed", String(selected));
  });
  characterSelectedSummary.textContent = `${character.name} · ${character.occupation || "调查员"} · ${character.source_label}`;
  btnCharacterConfirm.disabled = gameStarting;
  renderCharacterDetail(character);
}

function appendDetailSection(
  parent: HTMLElement,
  title: string,
  content: HTMLElement,
) {
  const section = createElement("section", "character-detail-section");
  section.append(createElement("h4", "", title), content);
  parent.appendChild(section);
}

function backstoryText(backstory: Record<string, unknown>, key: string): string {
  const value = backstory[key];
  return typeof value === "string" ? value.trim() : "";
}

function formatInventoryItem(item: unknown): string {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    const record = item as Record<string, unknown>;
    const label = record.label || record.name || record.id;
    if (typeof label === "string") {
      const count = typeof record.quantity === "number" ? ` × ${record.quantity}` : "";
      return `${label}${count}`;
    }
  }
  return String(item);
}

function renderCharacterDetail(character: CharacterOption) {
  const article = createElement("article", "character-dossier");
  const header = createElement("header", "character-detail-header");
  const identity = createElement("div", "character-detail-identity");
  identity.appendChild(createElement("h3", "", character.name));

  const identityParts = [character.occupation || "调查员"];
  if (character.age) identityParts.push(`${character.age} 岁`);
  if (character.era) identityParts.push(character.era);
  identity.appendChild(createElement("p", "", identityParts.join(" · ")));
  header.append(identity, createElement("span", "character-source-badge", character.source_label));
  article.appendChild(header);

  const derived = character.derived || {};
  const vitals = createElement("div", "character-vitals-grid");
  const vitalValues: [string, string | number | undefined][] = [
    ["HP", `${character.hp}/${character.max_hp}`],
    ["SAN", `${character.san}/${character.max_san}`],
    ["MP", derived.MP],
    ["幸运", derived.LUCK],
    ["移动", derived.MOV],
    ["伤害加值", derived.DB],
    ["体格", derived.BUILD],
    ["信用", character.credit_rating],
  ];
  for (const [label, value] of vitalValues) {
    if (value === undefined || value === null || value === "") continue;
    const stat = createElement("div", "character-vital");
    stat.append(createElement("span", "", label), createElement("strong", "", String(value)));
    vitals.appendChild(stat);
  }
  article.appendChild(vitals);

  const attributes = character.attributes || {};
  const attributeGrid = createElement("div", "character-attribute-grid");
  for (const id of Object.keys(ATTRIBUTE_LABELS)) {
    if (typeof attributes[id] !== "number") continue;
    const stat = createElement("div", "character-attribute");
    stat.append(
      createElement("span", "", `${ATTRIBUTE_LABELS[id]} ${id}`),
      createElement("strong", "", String(attributes[id])),
    );
    attributeGrid.appendChild(stat);
  }
  if (attributeGrid.childElementCount) appendDetailSection(article, "基础属性", attributeGrid);

  const skillList = createElement("div", "character-skill-list");
  for (const skill of character.top_skills || []) {
    const row = createElement("div", "character-skill");
    row.append(
      createElement("span", "", SKILL_LABELS[skill.id] || skill.id.replaceAll("_", " ")),
      createElement("strong", "", String(skill.value)),
    );
    skillList.appendChild(row);
  }
  if (skillList.childElementCount) appendDetailSection(article, "擅长技能", skillList);

  const backstory = character.backstory || {};
  const description = character.description?.trim() || backstoryText(backstory, "description");
  const background = backstoryText(backstory, "background");
  const beliefs = backstoryText(backstory, "beliefs");
  const traits = backstoryText(backstory, "traits");
  if (description || background || beliefs || traits) {
    const story = createElement("div", "character-story");
    for (const [label, value] of [
      ["外貌", description],
      ["经历", background],
      ["信念", beliefs],
      ["特质", traits],
    ]) {
      if (!value) continue;
      const paragraph = createElement("p");
      paragraph.append(createElement("strong", "", `${label}：`), document.createTextNode(value));
      story.appendChild(paragraph);
    }
    appendDetailSection(article, "人物档案", story);
  }

  const inventory = Array.isArray(character.inventory) ? character.inventory : [];
  if (inventory.length) {
    const list = createElement("ul", "character-inventory");
    for (const item of inventory) {
      list.appendChild(createElement("li", "", formatInventoryItem(item)));
    }
    appendDetailSection(article, "随身物品", list);
  }

  if (character.completed_modules || character.reputation) {
    const career = createElement(
      "div",
      "character-career-note",
      `完成案件 ${character.completed_modules} · 声望 ${character.reputation}`,
    );
    appendDetailSection(article, "调查履历", career);
  }

  characterDetail.replaceChildren(article);
}

btnStart.onclick = openCharacterSelection;
btnContinue.onclick = continueGame;
btnCharacterBack.onclick = () => {
  if (!gameStarting) showStartView("menu");
};
btnCharacterConfirm.onclick = startGame;
btnExit.hidden = !/\bElectron\//.test(navigator.userAgent);
btnExit.onclick = () => window.close();
btnNew.onclick = () => location.reload();

document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape"
    && isCharacterViewOpen()
    && !gameStarting
    && document.getElementById("module-import-overlay")?.classList.contains("hidden")
  ) {
    showStartView("menu");
  }
});
