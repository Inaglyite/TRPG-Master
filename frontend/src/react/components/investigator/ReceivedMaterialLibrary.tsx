import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";
import { ReceivedMaterials } from "./ReceivedMaterials";

/** Current recovery/identity scope invalidates outstanding private image reads. */
export function ReceivedMaterialLibrary() {
  const assets = useStructuredStore((state) => state.receivedAssets);
  const identity = useStructuredStore((state) => state.identity);
  const generation = useStructuredStore((state) => state.historyGeneration);
  const mode = useAppStore((state) => state.mode);
  const activeWorld = useAppStore((state) => state.activeWorldId);
  const account = useOnlineStore((state) =>
    JSON.stringify([state.authOrigin, state.user?.id]),
  );
  const scope = JSON.stringify([
    mode,
    identity.worldId,
    identity.investigatorId,
    generation,
    mode === "online" ? account : null,
  ]);
  if (!identity.investigatorId || activeWorld !== identity.worldId) return null;
  return (
    <ReceivedMaterials
      key={scope}
      entries={assets}
      worldId={identity.worldId}
      scope={scope}
      local={mode === "local"}
    />
  );
}
