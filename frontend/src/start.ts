/** Start-flow state machine and WebSocket command adapter. */

import { enableInput } from "./options";
import {
  clearTransientHandouts,
  openSavePanel,
  renderSavePanel,
} from "./panels";
import { resetGamePresentation, showGmThinking } from "./renderer";
import { useAppStore } from "./state/app-store";
import {
  useStartStore,
  type CharacterGroup,
  type ModuleOption,
} from "./state/start-store";
import { safeSend, sendImmediately } from "./ws";
import { useStructuredStore } from "./state/structured-store";
import { newRequestId } from "./protocol/structured";

let localCreationRequest: Record<string, unknown> | null = null;
let localCreationTimer: number | null = null;

function clearLocalCreationTimer() {
  if (localCreationTimer !== null) window.clearTimeout(localCreationTimer);
  localCreationTimer = null;
}

/** User explicitly leaves creation; an already committed world is not deleted. */
export function discardLocalCreationRequest() {
  clearLocalCreationTimer();
  localCreationRequest = null;
}

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
  if (localCreationRequest) {
    clearLocalCreationTimer();
    if (!sendImmediately(JSON.stringify(localCreationRequest))) {
      useStartStore.setState({
        gameStarting: false,
        hint: "连接未就绪，尚未发送新建请求；请重连后重试。",
      });
      return;
    }
    localCreationTimer = window.setTimeout(() => {
      localCreationTimer = null;
      useStartStore.setState({
        gameStarting: false,
        hint: "尚未确认新建结果；可重试原请求，或先查看存档。",
      });
    }, 15_000);
    return;
  }
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

/**
 * 从顶部“新游戏”回到应用内开局选择。
 *
 * 不重载 document，也不立刻改动服务端世界：用户仍需选定调查员并确认开始，
 * 才会沿用既有 start 命令重置世界。这既保留了原来的确认流程，也避免 Electron
 * reload 关闭正在使用的 WebSocket。
 */
export function returnToStartMenu() {
  if (getGameStarting()) return;
  clearRetry();
  retryAttempt = 0;
  discardLocalCreationRequest();
  enableInput(false);
  useAppStore.getState().setChoices([]);
  useAppStore.getState().setDialog(null);
  useAppStore.getState().setEnding(null);
  useStartStore.setState({
    gameStarted: false,
    gameStarting: false,
    view: "menu",
    moduleSwitchPending: false,
    hint: "",
  });
  // 回到开局页时刷新存档位/存档列表：开始新游戏后服务端不会主动推送，
  // 不刷新的话“从存档开始”会一直停留在连接初始化时的可用状态。
  safeSend(JSON.stringify({ type: "save_list" }));
}

function resetLocalGamePresentation() {
  resetGamePresentation();
  clearTransientHandouts();
  useAppStore.setState({
    character: null,
    clues: {},
    choices: [],
    dialog: null,
    ending: null,
    utilityOpen: false,
    characterPanelOpen: false,
    savePanelOpen: false,
    saves: [],
    worlds: [],
    renameSlotId: null,
    quickSaveState: "idle",
    notesText: "",
    notesRevision: 0,
    notesDirty: false,
    notesSaving: false,
    notesLoading: false,
    notesStatus: "",
    notesStatusKind: "",
  });
}

export function onGmTurnStart() {
  clearLocalCreationTimer();
  if (!getGameStarted()) {
    const startWasRequested = getGameStarting();
    clearRetry();
    retryAttempt = 0;
    // 此时 start 已被服务端接受；现在才清掉旧局 UI，避免用户只是打开
    // 开局选择就丢失当前画面或本地草稿。
    if (startWasRequested) {
      resetLocalGamePresentation();
    }
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
  const app = useAppStore.getState();
  if (
    app.mode === "local" &&
    (state.executionProfile === "structured_v1" ||
      useStructuredStore.getState().capabilities?.executionProfile ===
        "structured_v1")
  ) {
    if (!app.activeWorldId) {
      useStartStore.setState({ hint: "当前世界尚未同步，请等待连接恢复。" });
      return;
    }
    const draft = {
      source_world_id: app.activeWorldId,
      character_ref: state.selectedCharacterRef,
      execution_profile: state.executionProfile,
      keeper_mode: state.keeperMode,
    };
    const prior = localCreationRequest;
    // world_context precedes the receipt. A lost receipt may therefore leave
    // activeWorldId pointing at the newly created world already. Retrying must
    // preserve the original source and nonce rather than create another world.
    const same =
      prior &&
      Object.entries(draft)
        .filter(([key]) => key !== "source_world_id")
        .every(
          ([key, value]) =>
            JSON.stringify(prior[key]) === JSON.stringify(value),
        );
    if (prior && !same) {
      useStartStore.setState({
        hint: "此前的新建结果尚未确认，请先重试原设置或查看存档；返回开局选择后才可另建。",
      });
      return;
    }
    if (!same)
      localCreationRequest = {
        type: "local_start",
        request_id: newRequestId(),
        ...draft,
      };
  } else {
    localCreationRequest = null;
  }
  useStartStore.setState({ gameStarting: true, hint: "" });
  sendStartRequest();
}

/** Correlated server commit, before the fresh authoritative snapshot. */
export function onLocalStartResult(data: Record<string, unknown>) {
  if (
    useAppStore.getState().mode !== "local" ||
    !localCreationRequest ||
    data.request_id !== localCreationRequest.request_id
  )
    return;
  if (
    data.ok === true &&
    data.world_id !== useAppStore.getState().activeWorldId
  )
    return;
  clearLocalCreationTimer();
  clearRetry();
  if (data.ok !== true) {
    useStartStore.setState({
      gameStarting: false,
      hint: String(data.message || "新建尚未确认，请核对存档。"),
    });
    return;
  }
  if (!useStartStore.getState().gameStarted) resetLocalGamePresentation();
  localCreationRequest = null;
  useStartStore.setState({ gameStarting: false, gameStarted: true, hint: "" });
}

export function onStartTurnRejected(message: string, retryable: boolean) {
  if (!getGameStarting()) return false;
  clearRetry();
  if (localCreationRequest) {
    clearLocalCreationTimer();
    useStartStore.setState({ gameStarting: false, hint: message });
    return true; // Explicit local creation is retried only by its user.
  }
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
