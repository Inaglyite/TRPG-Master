import { create } from "zustand";
import { useStartStore } from "./start-store";
import { newRequestId } from "../protocol/structured";

export type SaveLoadPhase =
  "switching" | "restoring" | "history" | "character" | "presenting";

type SaveLoadProgress = {
  requestId: string;
  phase: SaveLoadPhase;
  startedAt: number;
  receivedAt?: number;
  serverMs?: number;
};

export const useSaveLoadStore = create<{
  progress: SaveLoadProgress | null;
}>(() => ({ progress: null }));

export function beginSaveLoad(phase: SaveLoadPhase): string {
  // Switching timelines and restoring slot_000 are one user operation.
  const previous = useSaveLoadStore.getState().progress;
  const progress = previous
    ? { ...previous, phase }
    : {
        requestId: `save-${newRequestId()}`,
        phase,
        startedAt: performance.now(),
      };
  useSaveLoadStore.setState({ progress });
  useStartStore.setState({ saveLoading: true, hint: "正在读取存档…" });
  if (!previous)
    console.info("[save-load] started", { request_id: progress.requestId });
  return progress.requestId;
}

export function advanceSaveLoad(
  phase: SaveLoadPhase,
  requestId?: string,
  serverMs?: number,
): void {
  const progress = useSaveLoadStore.getState().progress;
  if (!progress || (requestId && requestId !== progress.requestId)) return;
  useSaveLoadStore.setState({
    progress: {
      ...progress,
      phase,
      ...(phase === "presenting" ? { receivedAt: performance.now() } : {}),
      ...(typeof serverMs === "number" && Number.isFinite(serverMs)
        ? { serverMs }
        : {}),
    },
  });
}

export function finishSaveLoad(requestId: string) {
  const progress = useSaveLoadStore.getState().progress;
  if (!progress || progress.requestId !== requestId) return null;
  const now = performance.now();
  const metrics = {
    request_id: requestId,
    total_ms: Math.round(now - progress.startedAt),
    ...(progress.receivedAt !== undefined
      ? {
          response_ms: Math.round(progress.receivedAt - progress.startedAt),
          presentation_ms: Math.round(now - progress.receivedAt),
        }
      : {}),
    ...(progress.serverMs !== undefined
      ? { server_ms: progress.serverMs }
      : {}),
  };
  useSaveLoadStore.setState({ progress: null });
  useStartStore.setState({ saveLoading: false, hint: "" });
  console.info("[save-load] ready", metrics);
  return metrics;
}

export function failSaveLoad(reason: string, hint = ""): void {
  const progress = useSaveLoadStore.getState().progress;
  if (progress)
    console.info("[save-load] failed", {
      request_id: progress.requestId,
      phase: progress.phase,
      elapsed_ms: Math.round(performance.now() - progress.startedAt),
      reason,
    });
  useSaveLoadStore.setState({ progress: null });
  useStartStore.setState({ saveLoading: false, hint });
}
