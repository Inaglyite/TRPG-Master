import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { handleServerPayload } from "./ws";

function lastSystemMessage(): string {
  const messages = useMessageStore.getState().messages;
  return String(messages[messages.length - 1]?.text ?? "");
}

beforeEach(() => {
  useMessageStore.setState({ messages: [] });
});

describe("case_settled 结案提示", () => {
  it("联机成功：只讲结算结果，不声称写入本地长期履历", () => {
    useAppStore.setState({ mode: "online" });
    handleServerPayload({ type: "case_settled", ok: true });
    expect(lastSystemMessage()).toBe("案件已结算，房间已返回大厅。");
  });

  it("联机失败：案件结算失败并携带原因", () => {
    useAppStore.setState({ mode: "online" });
    handleServerPayload({ type: "case_settled", ok: false, error: "引擎忙" });
    expect(lastSystemMessage()).toBe("案件结算失败：引擎忙");
  });

  it("本地成功：保留长期履历语义", () => {
    useAppStore.setState({ mode: "local" });
    handleServerPayload({ type: "case_settled", ok: true });
    expect(lastSystemMessage()).toBe("案件经历已写入调查员长期履历。");
  });

  it("本地失败：履历写入失败并携带原因", () => {
    useAppStore.setState({ mode: "local" });
    handleServerPayload({ type: "case_settled", ok: false, error: "磁盘满" });
    expect(lastSystemMessage()).toBe("履历写入失败：磁盘满");
  });
});
