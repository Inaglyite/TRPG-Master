import { useAppStore } from "../../../state/app-store";
import {
  useInvestigatorPanelStore,
  useWorldPrefs,
} from "../../../state/investigator-panel-store";
import { CollapsibleCard } from "./CollapsibleCard";

// selector 必须返回稳定引用：无库存时复用同一空数组，避免无限重渲染。
const EMPTY_INVENTORY: string[] = [];

/**
 * 道具卡：权威库存是字符串数组，保留原始标签。
 * 相同标签聚合成一行显示“×N”，仅作展示合并，不冒充后端堆叠数量。
 */
export function InventoryCard() {
  const inventory = useAppStore(
    (state) => state.character?.inventory || EMPTY_INVENTORY,
  );
  const prefs = useWorldPrefs();
  const toggleCard = useInvestigatorPanelStore((state) => state.toggleCard);
  const openEditor = useInvestigatorPanelStore((state) => state.openEditor);

  const grouped: { label: string; count: number }[] = [];
  const indexByLabel = new Map<string, number>();
  for (const label of inventory) {
    const existing = indexByLabel.get(label);
    if (existing == null) {
      indexByLabel.set(label, grouped.length);
      grouped.push({ label, count: 1 });
    } else {
      grouped[existing].count += 1;
    }
  }

  return (
    <CollapsibleCard
      cardId="items"
      title="道具"
      emblem="✦"
      count={inventory.length}
      collapsed={prefs.collapsed.items}
      onToggle={() => toggleCard("items")}
      summary={`共 ${inventory.length} 件`}
    >
      {grouped.length === 0 && <div className="clue-empty">暂无随身道具</div>}
      {grouped.map(({ label, count }) => (
        <div className="inv-item-row" key={label} data-item={label}>
          <span className="inv-item-label">
            {label}
            {count > 1 && <span className="inv-item-count">×{count}</span>}
          </span>
          <button
            type="button"
            className="btn-ghost inv-row-btn inv-use-btn"
            onClick={() =>
              openEditor({
                kind: "use",
                itemLabel: label,
                usage: "",
                target: "",
              })
            }
          >
            使用
          </button>
        </div>
      ))}
    </CollapsibleCard>
  );
}
