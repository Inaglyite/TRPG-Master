import { useEffect, useState } from "react";

import { useAppStore } from "../../../state/app-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";
import { CharacterStatusCard } from "./CharacterStatusCard";
import { ClueCard, groupClues } from "./ClueCard";
import { InventoryCard } from "./InventoryCard";
import { PanelActionDialog } from "./PanelActionDialog";

/**
 * 调查员侧栏三卡片组合：人物状态 / 线索 / 道具。
 * 只组装卡片与共享的图片预览层；权威数据全部来自 app-store，
 * 本组件不产生游戏回合，也不保存业务数据副本。
 */
export function InvestigatorPanel() {
  const activeWorldId = useAppStore((state) => state.activeWorldId);
  const clues = useAppStore((state) => state.clues);
  const syncWorld = useInvestigatorPanelStore((state) => state.syncWorld);
  const reconcileClues = useInvestigatorPanelStore(
    (state) => state.reconcileClues,
  );
  const [image, setImage] = useState<{ src: string; alt: string } | null>(null);

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

  useEffect(() => {
    if (!image) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImage(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [image]);

  return (
    <>
      <div className="dossier-eyebrow">
        调查员档案<span>INVESTIGATOR</span>
      </div>
      <CharacterStatusCard />
      <ClueCard onImage={(src, alt) => setImage({ src, alt })} />
      <InventoryCard />
      <PanelActionDialog />
      {image && (
        <div
          className="handout-overlay"
          onClick={() => setImage(null)}
          role="presentation"
        >
          <img src={image.src} alt={image.alt} />
        </div>
      )}
    </>
  );
}
