import { AppHeader } from "./components/AppHeader";
import { ConnectionNotice } from "./components/ConnectionNotice";
import { DecisionModal, GameControls } from "./components/GameControls";
import { MessageList } from "./components/MessageList";
import { ModelSettingsPanel } from "./components/ModelSettingsPanel";
import {
  CharacterPanel,
  HandoutLayer,
  SavePanel,
} from "./components/PanelLayers";
import { StartScreen } from "./components/StartScreen";
import { UtilityPanel } from "./components/UtilityPanel";

export function GameShell() {
  return (
    <>
      <div id="app">
        <header id="header">
          <AppHeader />
        </header>
        <main id="main">
          <HandoutLayer />
          <div id="chat-panel">
            <ConnectionNotice />
            <MessageList />
            <GameControls />
          </div>
          <CharacterPanel />
        </main>
        <StartScreen />
        <DecisionModal />
      </div>
      <SavePanel />
      <ModelSettingsPanel />
      <UtilityPanel />
    </>
  );
}
