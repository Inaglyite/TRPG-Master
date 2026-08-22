import { useEffect, useRef } from "react";

import { desktopBridge } from "../../desktop";
import { continueGame, startGame, switchModule } from "../../start";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { CharacterDossier } from "./CharacterDossier";
import { ModelSettingsTrigger } from "./ModelSettingsPanel";
import { ModuleImporter } from "./ModuleImporter";
import { ModuleSelect } from "./ModuleSelect";
import { useModuleTransition } from "./module-transition";
import { useDelayedClose } from "./transitions";

/** 打开模组工坊（vendored TRPG Mod Editor）：Electron 走系统浏览器，浏览器新标签。 */
function openModWorkshop() {
  const bridge = desktopBridge();
  if (bridge) void bridge.openEditor();
  else window.open("/editor/", "_blank", "noopener");
}

async function startCommand(
  command: "startGame" | "continueGame" | "switchModule",
  value?: string,
) {
  if (command === "switchModule") switchModule(value || "");
  else if (command === "startGame") startGame();
  else continueGame();
}

export function StartScreen() {
  const state = useStartStore();
  const title = useAppStore((value) => value.title);
  const subtitle = useAppStore((value) => value.subtitle);
  const description = useAppStore((value) => value.description);
  const startButtonText = useAppStore((value) => value.startButtonText);
  const transition = useModuleTransition();
  // 模组切换期间冻结文案（ui-animation：内容不可在可见状态下被替换）。
  // flipping 阶段旧页面（背景+内容）整体 3D 翻走，冻结的旧文案就像印在
  // 被翻走的纸页上；翻页落定进入 entering 才放行新值——此刻旧页已翻走，
  // 换装用户不可见；idle（含 reduced-motion、切换失败回退）始终实时。
  const contentFreezeRef = useRef<{
    title: string;
    subtitle: string;
    description: string;
    startButtonText: string;
    activeModule: string;
    activeModuleTitle: string;
  } | null>(null);
  if (transition.phase === "flipping") {
    contentFreezeRef.current ??= {
      title,
      subtitle,
      description,
      startButtonText,
      activeModule: state.activeModule,
      activeModuleTitle: state.activeModuleTitle,
    };
  } else {
    contentFreezeRef.current = null;
  }
  const shown = contentFreezeRef.current ?? {
    title,
    subtitle,
    description,
    startButtonText,
    activeModule: state.activeModule,
    activeModuleTitle: state.activeModuleTitle,
  };
  // 开局/读档被服务端接受（gameStarted）后，整幕保持挂载 360ms 播退出动画，
  // 淡出揭示下方游戏画面，而不是瞬间 display:none（ui-animation 规范：成对进出）。
  const startedClose = useDelayedClose(!state.gameStarted, 360);
  const detailRef = useRef<HTMLElement>(null);
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
  // 切换调查员时档案面板回到顶部，避免沿用上一人的滚动位置。
  useEffect(() => {
    if (detailRef.current) detailRef.current.scrollTop = 0;
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
  if (!startedClose.rendered)
    return <div id="start-overlay" className="hidden" />;
  const overlayClass = [
    startedClose.closing ? "start-closing" : "",
    transition.phase === "flipping" ? "module-flipping" : "",
    transition.phase === "entering" ? "module-entering" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const boxClass =
    transition.phase === "entering" ? "module-entering" : undefined;
  // 主菜单与角色选择双挂载交叉过渡：非激活页用 view-off 退场到固定侧
  // （菜单在左、角色选择在右），由 CSS transition 完成重叠交叉，
  // 不出现“旧页消失 → 空背景 → 新页跳入”的闪烁（docs/ARCHITECTURE.md §9.2）。
  const menuOff = state.view !== "menu";
  const charactersOff = state.view !== "characters";
  const layerImage = (value: string | null) =>
    ({ "--layer-image": value || "var(--ui-start-bg)" }) as React.CSSProperties;
  // 翻页期间真实内容盒随旧页一起 3D 翻走（内容已被冻结为旧模组）；
  // 落定后回到正常位置播 entering 淡入。
  const startBox = (
    <div id="start-box" className={boxClass}>
      <div id="start-view-stack">
        <section
          id="start-menu-view"
          className={`start-view${menuOff ? " view-off" : ""}`}
          aria-label="主菜单"
          aria-hidden={menuOff}
        >
          <div className="start-brand">
            <div id="start-title" className="fx-glow">
              {shown.title}
            </div>
            <div id="start-subtitle">{shown.subtitle}</div>
            {shown.description && (
              <div id="start-description">{shown.description}</div>
            )}
          </div>
          <div id="module-selector">
            <label id="module-select-label">当前模组</label>
            <div className="module-select-row">
              <ModuleSelect
                options={state.modules}
                value={shown.activeModule}
                disabled={state.moduleSwitchPending}
                labelledBy="module-select-label"
                onSelect={(id) => void startCommand("switchModule", id)}
              />
              <ModuleImporter />
              <button
                type="button"
                className="btn-ghost module-workshop-link"
                title="打开 TRPG Mod Editor 创作/编辑模组"
                onClick={openModWorkshop}
              >
                模组工坊
              </button>
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
                {shown.startButtonText || "开始新游戏"}
              </span>
            </button>
            <button
              id="btn-continue"
              className="start-art-button art-plaque"
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
        <section
          id="character-select-view"
          className={`start-view${charactersOff ? " view-off" : ""}`}
          aria-hidden={charactersOff}
        >
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
              <div id="character-module-name">{shown.activeModuleTitle}</div>
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
                        ? `${shown.activeModuleTitle} 特色调查员`
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
            <aside id="character-detail" ref={detailRef}>
              {selected ? (
                <CharacterDossier key={selected.id} character={selected} />
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
      </div>
    </div>
  );
  return (
    <div
      id="start-overlay"
      className={overlayClass || undefined}
      aria-hidden={startedClose.closing || undefined}
    >
      {transition.incomingBg !== undefined && (
        <div
          className="start-bg-layer incoming"
          aria-hidden="true"
          style={layerImage(transition.incomingBg ?? null)}
        />
      )}
      {transition.phase === "flipping" ? (
        <div className="module-page-flip">
          <div
            className="start-bg-layer outgoing"
            aria-hidden="true"
            style={layerImage(transition.outgoingBg)}
          />
          <div className="module-page-flip-shade" aria-hidden="true" />
          {startBox}
        </div>
      ) : (
        startBox
      )}
      {transition.phase === "flipping" && (
        <div className="module-page-edge" aria-hidden="true" />
      )}
    </div>
  );
}
