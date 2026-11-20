import { listSoloSaves, mayHaveSoloSave } from "../../../api/solo-saves";
import { useEffect, useRef, useState } from "react";

import { createSoloWorld, errorMessage } from "../../../online";
import { desktopBridge } from "../../../desktop";
import { useAppStore } from "../../../state/app-store";
import { resetOnlineState, useOnlineStore } from "../../../state/online-store";
import { SoloHome } from "./SoloHome";

export function SoloLobbyScreen() {
  const account = useOnlineStore((state) => state.user?.id);
  const origin = useOnlineStore((state) => state.authOrigin);
  const preferenceKey = JSON.stringify(["trpg-solo-module", origin, account]);
  const worlds = useOnlineStore((state) => state.worlds);
  const worldsStatus = useOnlineStore((state) => state.worldsStatus);
  const modules = useOnlineStore((state) => state.modules);
  const setMode = useAppStore((state) => state.setMode);

  const [moduleId, setModuleId] = useState(() => {
    try {
      return localStorage.getItem(preferenceKey) ?? "";
    } catch {
      return "";
    }
  });
  function selectModule(id: string) {
    setModuleId(id);
    try {
      localStorage.setItem(preferenceKey, id);
    } catch {
      /* Storage is optional. */
    }
  }

  const soloWorlds = worlds.filter(
    (world) => world.metadata?.play_mode === "solo",
  );
  const moduleTitle = (id: string) =>
    modules.find((module) => module.id === id)?.title ?? id;
  const fallbackModuleId = useRef("");
  const explicitModule = modules.find((item) => item.id === moduleId)?.id;
  const candidateModule =
    explicitModule ??
    modules.find((item) => item.id === soloWorlds[0]?.module)?.id ??
    modules[0]?.id ??
    "";
  if (explicitModule) fallbackModuleId.current = explicitModule;
  else if (
    !fallbackModuleId.current ||
    !modules.some((item) => item.id === fallbackModuleId.current)
  ) {
    fallbackModuleId.current = candidateModule;
  }
  // 排序刷新只改变候选列表；没有明确选择时，当前模组同样保持稳定。
  const selectedModule = explicitModule ?? fallbackModuleId.current;

  async function backToModeSelect() {
    const bridge = desktopBridge();
    if (bridge) {
      const result = await bridge.returnToLauncher();
      if (!result.ok) {
        useOnlineStore.setState({
          authError: result.error ?? "无法返回模式选择",
        });
      }
      return;
    }
    resetOnlineState();
    setMode("select");
  }

  const requestId = useRef(0);
  const [savesBusy, setSavesBusy] = useState(false);
  const [savesError, setSavesError] = useState("");
  useEffect(() => {
    setSavesBusy(false);
    setSavesError("");
    return () => {
      requestId.current++;
    };
  }, [selectedModule, account, origin]);

  async function openSaves() {
    const id = ++requestId.current;
    setSavesBusy(true);
    setSavesError("");
    try {
      const slots = await listSoloSaves(
        selectedModule,
        moduleTitle(selectedModule),
      );
      if (id !== requestId.current || useOnlineStore.getState().view !== "solo")
        return;
      useAppStore.setState({
        adventures: slots,
        adventuresReady: true,
        activeWorldId: null,
        savePanelView: { name: "adventures" },
        savePanelMode: "load",
        savePanelOpen: true,
      });
    } catch (err) {
      if (id === requestId.current)
        setSavesError(errorMessage(err, "无法读取存档，请重试"));
    } finally {
      if (id === requestId.current) setSavesBusy(false);
    }
  }

  return (
    <SoloHome
      selectedId={selectedModule}
      onSelect={selectModule}
      onCreate={() =>
        void createSoloWorld(selectedModule, moduleTitle(selectedModule))
      }
      hasSaves={
        worldsStatus === "ready" &&
        soloWorlds.some((world) => mayHaveSoloSave(world, selectedModule))
      }
      savesBusy={savesBusy}
      savesError={savesError}
      onSaves={openSaves}
      onBack={() => void backToModeSelect()}
    />
  );
}
