import type { KeeperMode } from "./structured";

export function playSupportNote(mode: KeeperMode): string {
  return mode === "human"
    ? "人类主持可处理调查、社交、战斗与结案；具体操作以当前服务器开放的功能为准。"
    : "AI 行动依赖模型和模组支持；复杂流程可由人类主持接管并核对结算。具体操作以当前服务器开放的功能为准。";
}
