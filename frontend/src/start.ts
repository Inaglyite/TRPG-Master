/** Start-flow state machine and WebSocket command adapter. */

import { enableInput } from "./options";
import { openSavePanel, renderSavePanel } from "./panels";
import { showGmThinking } from "./renderer";
import {
  useStartStore,
  type CharacterGroup,
  type ModuleOption,
} from "./state/start-store";
import { safeSend } from "./ws";

let retryTimer: number | null = null;
let retryAttempt = 0;
const retryDelays = [400, 700, 1000, 1500, 2200, 3000];

function clearRetry() {
  if (retryTimer !== null) window.clearTimeout(retryTimer);
  retryTimer = null;
}

function sendStartRequest() {
  const state = useStartStore.getState();
  if (!state.gameStarting || !state.selectedCharacterRef) return;
  safeSend(
    JSON.stringify({
      type: "start",
      character_ref: state.selectedCharacterRef,
    }),
  );
}

export function getGameStarted() {
  return useStartStore.getState().gameStarted;
}

export function getGameStarting() {
  return useStartStore.getState().gameStarting;
}

export function onGmTurnStart() {
  if (!getGameStarted()) {
    clearRetry();
    retryAttempt = 0;
    useStartStore.setState({ gameStarting: false, gameStarted: true });
  }
  enableInput(false);
  showGmThinking();
}

export function resetStartButton() {
  clearRetry();
  retryAttempt = 0;
  useStartStore.setState((state) => ({
    gameStarting: false,
    moduleSwitchPending: false,
    hint: state.hint === "正在切换模组…" ? "" : state.hint,
  }));
}

export function startGame() {
  const state = useStartStore.getState();
  if (state.gameStarting || !state.selectedCharacterRef) return;
  clearRetry();
  retryAttempt = 0;
  useStartStore.setState({ gameStarting: true, hint: "" });
  sendStartRequest();
}

export function onStartTurnRejected(message: string, retryable: boolean) {
  if (!getGameStarting()) return false;
  clearRetry();
  if (retryable && retryAttempt < retryDelays.length) {
    const delay = retryDelays[retryAttempt++];
    useStartStore.setState({ hint: `${message} 正在自动重试……` });
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      sendStartRequest();
    }, delay);
    return true;
  }
  resetStartButton();
  useStartStore.setState({ hint: message });
  return true;
}

export function continueGame() {
  if (useStartStore.getState().hasSaves) openSavePanel("load");
}

export function onSaveAvailable(data: any) {
  if (data.has_save) useStartStore.setState({ hasSaves: true });
}

export function onSaveList(data: any) {
  const saves = Array.isArray(data.saves) ? data.saves : [];
  useStartStore.setState({ hasSaves: saves.length > 0, hint: "" });
  renderSavePanel(saves);
}

export function populateModuleList(modules: ModuleOption[], active: string) {
  const state = useStartStore.getState();
  const changed = Boolean(state.activeModule && state.activeModule !== active);
  useStartStore.setState({
    modules: Array.isArray(modules) ? modules : [],
    activeModule: active,
    activeModuleTitle:
      modules.find((module) => module.id === active)?.title || active,
    moduleSwitchPending: false,
    ...(changed
      ? {
          selectedCharacterRef: null,
          selectedCharacterId: "",
          characterGroups: [],
          charactersReady: false,
          hasSaves: false,
          view: "menu" as const,
        }
      : {}),
  });
}
export function switchModule(module: string) {
  const state = useStartStore.getState();
  if (!module || module === state.activeModule || state.moduleSwitchPending)
    return;
  useStartStore.setState({ moduleSwitchPending: true, hint: "正在切换模组…" });
  safeSend(JSON.stringify({ type: "switch_module", module }));
}

export function populateCharacterList(groups: CharacterGroup[]) {
  const normalized = Array.isArray(groups) ? groups : [];
  const characters = normalized.flatMap((group) => group.characters || []);
  const state = useStartStore.getState();
  const selected =
    characters.find(
      (character) => character.id === state.selectedCharacterId,
    ) || characters[0];
  useStartStore.setState({
    characterGroups: normalized,
    charactersReady: true,
    selectedCharacterId: selected?.id || "",
    selectedCharacterRef: selected?.ref || null,
  });
}
