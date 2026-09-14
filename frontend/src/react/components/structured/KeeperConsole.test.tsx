import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  STRUCTURED_CAPABILITIES_WIRE,
  EVENT_FIXTURES,
  WORLD_ID,
} from "../../../protocol/structured-fixtures";
import { setStructuredSender } from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { KeeperConsole, keeperAuthorized } from "./KeeperConsole";

let sent: Record<string, unknown>[] = [];

function enableStructured(keeper: {
  user_id: string | null;
  mode: string | null;
}) {
  useStructuredStore.getState().applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
  const enrollment = {
    ...EVENT_FIXTURES.snapshot,
    payload: {
      ...EVENT_FIXTURES.snapshot.payload,
      keeper: keeper.user_id ? keeper : { mode: keeper.mode },
    },
  };
  useStructuredStore
    .getState()
    .applySnapshot(enrollment.payload as Record<string, unknown>, WORLD_ID);
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    inputEnabled: true,
    activeWorldId: WORLD_ID,
  });
  useOnlineStore.setState({ activeInvestigatorId: "inv-alice", user: null });
  sent = [];
  setStructuredSender((payload) => {
    sent.push(payload as Record<string, unknown>);
    return true;
  });
});

describe("keeper 授权", () => {
  it("房主不等于 keeper：没有 keeper 投影就不授权", () => {
    expect(keeperAuthorized(null, null, "user-owner")).toBe(false);
  });

  it("服务端把当前用户标为 keeper 才授权", () => {
    expect(keeperAuthorized("user-keeper", "human", "user-keeper")).toBe(true);
    expect(keeperAuthorized("user-keeper", "human", "user-owner")).toBe(false);
  });

  it("本地单机没有账号身份时：服务端标出 keeper 即为本机操作者", () => {
    expect(keeperAuthorized(null, "human", null, true)).toBe(true);
    expect(keeperAuthorized("local-operator", "human", null, true)).toBe(true);
    // 云端（非本地）没有身份时不能凭 keeper 投影自封主持。
    expect(keeperAuthorized("someone-else", "human", null, false)).toBe(false);
  });
});

describe("KeeperConsole", () => {
  it("没有 keeper_console 能力时不渲染入口", () => {
    render(<KeeperConsole />);
    expect(screen.queryByTestId("btn-keeper-console")).not.toBeInTheDocument();
  });

  it("有能力和 keeper 身份时显示入口与命令清单", () => {
    enableStructured({ user_id: "user-keeper", mode: "human" });
    useOnlineStore.setState({
      user: { id: "user-keeper", username: "keeper" },
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    for (const kind of [
      "publish_message",
      "grant_clue",
      "request_check",
      "adjust_stat",
      "move_party",
      "advance_time",
      "resolve_intent",
      "present_handout",
      "set_npc_presence",
      "record_fact",
      "transfer_item",
      "use_item",
      "resolve_check",
      "present_information",
    ]) {
      expect(screen.getByTestId(`keeper-cmd-${kind}`)).toBeInTheDocument();
    }
  });

  it("非 keeper 打开时命令禁用并说明需要权限", () => {
    enableStructured({ user_id: "user-keeper", mode: "human" });
    useOnlineStore.setState({
      user: { id: "user-player", username: "player" },
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByTestId("keeper-unauthorized")).toBeInTheDocument();
    expect(screen.getByTestId("keeper-cmd-grant_clue")).toBeDisabled();
  });

  it("NPC 发言提交的 payload 使用 M0 speaker 形态", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-publish_message"));

    fireEvent.change(
      document.querySelector('[data-field="speaker_kind"] select')!,
      {
        target: { value: "npc" },
      },
    );
    fireEvent.change(
      document.querySelector(
        '[data-field="speaker_id"] select, [data-field="speaker_id"] input',
      )!,
      {
        target: { value: "john_whitcroft" },
      },
    );
    fireEvent.change(
      document.querySelector(
        '[data-field="text"] select, [data-field="text"] input',
      )!,
      {
        target: { value: "停尸房不对外开放。" },
      },
    );
    fireEvent.click(screen.getByTestId("keeper-submit"));

    expect(sent).toHaveLength(1);
    const frame = sent[0];
    expect(frame.type).toBe("command_request");
    expect(frame.kind).toBe("publish_message");
    expect(frame.protocol_version).toBe(1);
    expect(frame.command_id).toBeTruthy();
    expect(frame.expected_revision).toBe(12);
    expect(frame.payload).toEqual({
      speaker: { kind: "npc", id: "john_whitcroft" },
      audience: { kind: "public" },
      text: "停尸房不对外开放。",
    });
  });

  it("必填缺失时不发帧，并在表单里报错（草稿保留）", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-grant_clue"));
    fireEvent.change(
      document.querySelector(
        '[data-field="clue_id"] select, [data-field="clue_id"] input',
      )!,
      {
        target: { value: "clue_death_certificate" },
      },
    );
    fireEvent.click(screen.getByTestId("keeper-submit"));

    expect(sent).toHaveLength(0);
    expect(screen.getByRole("alert")).toHaveTextContent("接收调查员");
    // 草稿仍在：线索选择没有被清空。
    expect(
      document.querySelector(
        '[data-field="clue_id"] select, [data-field="clue_id"] input',
      )!,
    ).toHaveValue("clue_death_certificate");
  });

  it("私发线索带接收者与依据；提交后提示“接收不等于执行成功”", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-grant_clue"));
    fireEvent.change(
      document.querySelector(
        '[data-field="clue_id"] select, [data-field="clue_id"] input',
      )!,
      {
        target: { value: "clue_death_certificate" },
      },
    );
    fireEvent.change(
      document.querySelector(
        '[data-field="recipient_investigator_ids"] select, [data-field="recipient_investigator_ids"] input',
      )!,
      { target: { value: "inv-alice" } },
    );
    fireEvent.change(
      document.querySelector(
        '[data-field="basis"] select, [data-field="basis"] input',
      )!,
      {
        target: { value: "医生当面说明" },
      },
    );
    fireEvent.click(screen.getByTestId("keeper-submit"));

    expect(sent[0].kind).toBe("grant_clue");
    expect(sent[0].payload).toEqual({
      clue_id: "clue_death_certificate",
      recipient_investigator_ids: ["inv-alice"],
      basis: "医生当面说明",
    });
    expect(screen.getByText(/不代表执行成功/)).toBeInTheDocument();
  });

  it("待处理行动区显示玩家请求的状态", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.getState().registerOutgoing({
      requestId: "req-player-1",
      kind: "move",
      label: "前往",
      payload: {},
      digest: "",
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByText("req-player-1")).toBeInTheDocument();
    expect(screen.getByText("待处理行动")).toBeInTheDocument();
  });

  it("授权资料：keeper 看到完整线索登记表；未提供主持资料时如实说明", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByText("授权模组资料")).toBeInTheDocument();
    // 快照里的线索是 keeper 视角的完整登记表。
    expect(screen.getByText(/clue_death_certificate/)).toBeInTheDocument();
    expect(screen.getByTestId("keeper-material-missing")).toHaveTextContent(
      "服务端未提供主持专属资料条目",
    );
  });

  it("服务端下发 keeper_material 时直接显示", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.getState().applySnapshot(
      {
        ...EVENT_FIXTURES.snapshot.payload,
        keeper_material: [
          { title: "惠特克罗夫特的秘密", text: "他在压力下签署了死亡证明。" },
        ],
      } as Record<string, unknown>,
      WORLD_ID,
    );
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByTestId("keeper-material")).toHaveTextContent(
      "惠特克罗夫特的秘密",
    );
    expect(screen.getByTestId("keeper-material")).toHaveTextContent(
      "他在压力下签署了死亡证明。",
    );
  });

  it("提供存档与续团入口", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByTestId("keeper-save")).toBeInTheDocument();
    expect(screen.getByTestId("keeper-load")).toBeInTheDocument();
    expect(screen.getByTestId("keeper-save-panel")).toBeInTheDocument();
  });

  it("协议不可用时命令禁用并显示原因", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore
      .getState()
      .setProtocolNotice("服务端使用不同版本的协议。");
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByRole("alert")).toHaveTextContent("不同版本的协议");
    const advance = screen.getByTestId("keeper-cmd-advance_time");
    expect(advance).toBeDisabled();
    expect(advance).toHaveAttribute("title", "服务端使用不同版本的协议。");
    // 被阻断时不会出现可提交的表单。
    expect(screen.queryByTestId("keeper-submit")).not.toBeInTheDocument();
  });
});
