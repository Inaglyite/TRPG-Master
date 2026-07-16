import { create } from "zustand";

export type CharacterRef = {
  source: string;
  id?: string;
  module?: string;
  file?: string;
  path?: string;
};
export type CharacterOption = {
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
export type CharacterGroup = {
  id: string;
  title: string;
  characters: CharacterOption[];
};
export type ModuleOption = { id: string; title: string };

type StartState = {
  gameStarted: boolean;
  gameStarting: boolean;
  view: "menu" | "characters";
  modules: ModuleOption[];
  activeModule: string;
  activeModuleTitle: string;
  moduleSwitchPending: boolean;
  characterGroups: CharacterGroup[];
  charactersReady: boolean;
  selectedCharacterId: string;
  selectedCharacterRef: CharacterRef | null;
  hasSaves: boolean;
  hint: string;
};

export const useStartStore = create<StartState>(() => ({
  gameStarted: false,
  gameStarting: false,
  view: "menu",
  modules: [],
  activeModule: "",
  activeModuleTitle: "当前模组",
  moduleSwitchPending: false,
  characterGroups: [],
  charactersReady: false,
  selectedCharacterId: "",
  selectedCharacterRef: null,
  hasSaves: false,
  hint: "正在读取存档…",
}));
