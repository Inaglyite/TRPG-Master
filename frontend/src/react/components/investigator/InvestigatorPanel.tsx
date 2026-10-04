import { useCallback, useEffect, useRef, useState } from "react";

import { useAppStore } from "../../../state/app-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";
import { usePanelClues } from "../../../investigator-panel-view";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";
import { ClueImagePreview, type ClueImageSelection } from "./ClueImagePreview";
import { CharacterStatusCard } from "./CharacterStatusCard";
import { ClueCard, groupClues } from "./ClueCard";
import { InventoryCard } from "./InventoryCard";
import { PanelActionDialog } from "./PanelActionDialog";
import { StructuredActionDialog } from "./StructuredActionDialog";

/**
 * 调查员侧栏三卡片组合：人物状态 / 线索 / 道具。
 * 只组装卡片与共享的图片预览层；权威数据全部来自 app-store，
 * 本组件不产生游戏回合，也不保存业务数据副本。
 */
export function InvestigatorPanel() {
  const activeWorldId = useAppStore((state) => state.activeWorldId);
  const mode = useAppStore((state) => state.mode);
  const { clues, path } = usePanelClues();
  const investigatorId = useStructuredStore(
    (state) => state.identity.investigatorId,
  );
  const onlineIdentity = useOnlineStore((state) => {
    const member = state.members.find(
      (entry) => entry.user_id === state.user?.id,
    );
    return JSON.stringify([
      state.authOrigin,
      state.user?.id,
      member?.role,
      member?.investigator?.character_key,
    ]);
  });
  const scope = JSON.stringify([
    mode,
    activeWorldId,
    path,
    investigatorId,
    mode === "online" ? onlineIdentity : null,
  ]);
  const syncWorld = useInvestigatorPanelStore((state) => state.syncWorld);
  const reconcileClues = useInvestigatorPanelStore(
    (state) => state.reconcileClues,
  );
  const [image, setImage] = useState<ClueImageSelection | null>(null);
  const imageTrigger = useRef<HTMLButtonElement>(null);
  const closeImage = useCallback(() => setImage(null), []);
  const authorizedSources = Object.values(clues)
    .flatMap((items) =>
      items.map(
        (item) => item.asset?.asset_data_uri || item.asset?.asset_url || "",
      ),
    )
    .filter(Boolean);

  // 世界/时间线作用域：切世界关闭编辑器草稿、重置为该世界的 UI 偏好。
  useEffect(() => {
    syncWorld(activeWorldId);
  }, [activeWorldId, syncWorld]);

  // 线索集合对齐：裁剪不存在条目的展开态；维护“新增”标记基线。
  useEffect(() => {
    const keys = groupClues(clues).groups.flatMap((group) =>
      group.items.map((entry) => entry.key),
    );
    reconcileClues(keys);
  }, [clues, reconcileClues]);

  return (
    <>
      <div className="dossier-eyebrow">
        调查员档案<span>INVESTIGATOR</span>
      </div>
      <CharacterStatusCard />
      <ClueCard
        onImage={(source, label, trigger) => {
          imageTrigger.current = trigger;
          setImage({ source, label, scope });
        }}
      />
      <InventoryCard />
      <PanelActionDialog />
      <StructuredActionDialog />
      <ClueImagePreview
        selection={image}
        scope={scope}
        authorizedSources={authorizedSources}
        returnFocus={imageTrigger}
        onClose={closeImage}
      />
    </>
  );
}
