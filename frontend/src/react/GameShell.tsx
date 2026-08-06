import { useAppStore } from "../state/app-store";
import { AppHeader } from "./components/AppHeader";
import { ConnectionNotice } from "./components/ConnectionNotice";
import { DecisionModal, GameControls } from "./components/GameControls";
import { MessageList } from "./components/MessageList";
import { ModeSelectScreen } from "./components/ModeSelectScreen";
import { ModelSettingsPanel } from "./components/ModelSettingsPanel";
import { OnlineShell } from "./components/online/OnlineShell";
import { OnlineRoomDock } from "./components/online/OnlineRoomDock";
import {
  CharacterPanel,
  HandoutLayer,
  SavePanel,
} from "./components/PanelLayers";
import { StartScreen } from "./components/StartScreen";
import { UtilityPanel } from "./components/UtilityPanel";

export function GameShell() {
  const mode = useAppStore((state) => state.mode);

  return (
    <>
      <div id="app">
        <header id="header">
          <AppHeader />
        </header>
        <main id="main">
          <div id="chat-panel">
            {mode === "online" && <OnlineRoomDock />}
            <HandoutLayer />
            <ConnectionNotice />
            <MessageList />
            <GameControls />
          </div>
          <CharacterPanel />
        </main>
        {mode === "local" && <StartScreen />}
        <DecisionModal />
      </div>
      <SavePanel />
      <ModelSettingsPanel />
      <UtilityPanel />
      {mode === "select" && <ModeSelectScreen />}
      {mode === "online" && <OnlineShell />}
    </>
  );
}
