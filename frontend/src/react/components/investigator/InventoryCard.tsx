import { usePanelItems } from "../../../investigator-panel-view";
import {
  beginUseItem,
  useUnavailableReason,
} from "../../../investigator-structured-actions";
import {
  useInvestigatorPanelStore,
  useWorldPrefs,
} from "../../../state/investigator-panel-store";
import { CollapsibleCard } from "./CollapsibleCard";

/**
 * 道具卡：
 * - legacy 世界：权威库存是字符串数组，同标签合并显示“×N”（仅展示合并）。
 * - structured_v1 世界：使用服务端投影的物品 ID、数量与可用操作，
 *   提交结构化请求；标签不再作为实体标识。
 * 两条路径都不在前端扣减数量。
 */
export function InventoryCard() {
  const { path, items } = usePanelItems();
  const prefs = useWorldPrefs();
  const toggleCard = useInvestigatorPanelStore((state) => state.toggleCard);
  const openEditor = useInvestigatorPanelStore((state) => state.openEditor);

  const totalCount = items.reduce(
    (sum, item) => sum + (item.quantity ?? item.legacyCount),
    0,
  );

  return (
    <CollapsibleCard
      cardId="items"
      title="道具"
      emblem="✦"
      count={totalCount}
      collapsed={prefs.collapsed.items}
      onToggle={() => toggleCard("items")}
      summary={`共 ${totalCount} 件`}
    >
      {path === "structured" && (
        <p className="inv-path-note" data-path="structured">
          使用会先提交申请；主持确认后才结算效果和消耗。
        </p>
      )}
      {items.length === 0 && (
        <div className="clue-empty">
          {path === "structured" ? "暂无可使用的随身道具" : "暂无随身道具"}
        </div>
      )}
      {items.map((item) => {
        const blocked = useUnavailableReason(item.id);
        return (
          <div
            className="inv-item-row"
            key={item.id ?? item.label}
            data-item={item.label}
            data-item-id={item.id ?? undefined}
          >
            <span className="inv-item-label">
              {item.label}
              {(item.quantity ?? item.legacyCount) > 1 && (
                <span className="inv-item-count">
                  ×{item.quantity ?? item.legacyCount}
                </span>
              )}
            </span>
            <button
              type="button"
              className="btn-ghost inv-row-btn inv-use-btn"
              disabled={blocked !== null}
              title={blocked ?? "申请使用这件道具，确认前不会扣减数量"}
              onClick={() => {
                if (path === "structured") {
                  beginUseItem({
                    itemId: item.id,
                    label: item.label,
                    operations: item.operations,
                    quantity: item.quantity,
                  });
                  return;
                }
                openEditor({
                  kind: "use",
                  itemLabel: item.label,
                  usage: "",
                  target: "",
                });
              }}
            >
              使用
            </button>
          </div>
        );
      })}
    </CollapsibleCard>
  );
}
