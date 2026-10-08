import type { KeeperMode } from "../../../protocol/structured";
import { playSupportNote } from "../../../protocol/play-support";

export function KeeperModeSelect({
  value,
  disabled,
  onChange,
}: {
  value: KeeperMode;
  disabled: boolean;
  onChange: (value: KeeperMode) => void;
}) {
  return (
    <label className="online-field">
      <span>主持方式</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as KeeperMode)}
      >
        <option value="human">人类主持</option>
        <option value="assisted">AI 辅助主持（人类批准后执行）</option>
        <option value="agent">AI 主持</option>
      </select>
      <span className="online-section-desc">
        {value === "human"
          ? "无需模型配置；由主持处理行动和发布叙事。"
          : "需要自行配置模型与 API Key；主持可以接管或重试暂停请求。"}{" "}
        {playSupportNote(value)}
      </span>
    </label>
  );
}
