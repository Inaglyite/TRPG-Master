import { InvestigatorPanel } from "./investigator/InvestigatorPanel";

/**
 * 兼容包装：旧的单一混排面板已拆为三卡片（人物状态/线索/道具），
 * 实现见 ./investigator/。保留本导出以免牵动既有入口与测试。
 */
export function CharacterPanelContent() {
  return <InvestigatorPanel />;
}
