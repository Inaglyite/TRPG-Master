import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  EVENT_FIXTURES,
  STRUCTURED_CAPABILITIES_WIRE,
  WORLD_ID,
} from "../../../protocol/structured-fixtures";
import { setStructuredSender } from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import {
  ActionStatusCard,
  CheckRequestCard,
  StructuredDock,
} from "./StructuredCards";

vi.mock("../../../renderer", () => ({
  onDice: vi.fn(),
  onNarrativeChunk: vi.fn(),
  onNarrativeSegment: vi.fn(),
}));
vi.mock("../../../panels", () => ({ updateCharPanel: vi.fn() }));

function request(over: Record<string, unknown> = {}) {
  const now = Date.now();
  return {
    requestId: "req-1",
    kind: "move" as const,
    label: "前往",
    status: "processing" as const,
    outcome: null,
    detail: "",
    errorCode: null,
    errorMessage: null,
    payload: {},
    digest: "",
    sends: 1,
    createdAt: now,
    updatedAt: now,
    awaitingAck: false,
    awaiting: null,
    ...over,
  };
}

function check(over: Record<string, unknown> = {}) {
  return {
    checkRequestId: "chk-1",
    investigatorId: "inv-alice",
    skill: "说服",
    difficulty: "regular",
    bonusPenalty: 0,
    attempt: "向医生说明来意",
    knownCost: "可能需要出示证件",
    visibility: "public",
    status: "pending" as const,
    result: null,
    updatedAt: Date.now(),
    ...over,
  };
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    inputEnabled: true,
  });
  useOnlineStore.setState({ activeInvestigatorId: null });
});

describe("ActionStatusCard：接收不等于成功", () => {
  it("processing 只说明守秘人处理中，不显示成功字样", () => {
    render(<ActionStatusCard request={request({ status: "processing" })} />);
    expect(screen.getByText("守秘人处理中")).toBeInTheDocument();
    expect(screen.queryByText(/结果：成功/)).not.toBeInTheDocument();
  });

  it("awaiting_player 显示尚未执行的待办与已告知条件，且不逼玩家点确认", () => {
    render(
      <ActionStatusCard
        request={request({
          status: "awaiting_player",
          awaiting: {
            kind: "move",
            note: "尚未出发前往停尸房",
            target: "",
            destinationSceneId: "morgue",
            disclosed: ["停尸房需要值班医生放行"],
            reason: "等待玩家决定是否现在联系医生",
          },
        })}
      />,
    );
    expect(screen.getByTestId("structured-awaiting")).toBeInTheDocument();
    expect(
      screen.getByText(/尚未执行：尚未出发前往停尸房/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/已告知：停尸房需要值班医生放行/),
    ).toBeInTheDocument();
    expect(screen.getByText(/直接说话回应就行/)).toBeInTheDocument();
    // 过渡回合不是强制确认弹窗：不能只给“继续/取消”
    expect(screen.queryByRole("button", { name: /继续/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /取消/ })).toBeNull();
  });

  it("取消后不再有可执行按钮：旧卡片不能继续表现为可执行", () => {
    render(
      <ActionStatusCard
        request={request({
          status: "cancelled",
          awaiting: {
            kind: "move",
            note: "尚未出发",
            target: "",
            destinationSceneId: "morgue",
            disclosed: [],
            reason: "",
          },
          awaitingAck: false,
          errorCode: null,
        })}
      />,
    );
    expect(screen.queryByTestId("structured-awaiting")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("已取消")).toBeInTheDocument();
  });

  it("暂停必须可见（含可操作原因），且没有转圈", () => {
    const { container } = render(
      <ActionStatusCard
        request={request({
          status: "paused",
          detail:
            "守秘人助手本次没有产出内容（finish_reason=length，生效 max_tokens=16000）。",
        })}
      />,
    );
    expect(screen.getByText("已暂停（可恢复）")).toBeInTheDocument();
    expect(screen.getByText(/finish_reason=length/)).toBeInTheDocument();
    // 结构化卡片不引入无限转圈：没有进度条/加载指示器
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
  });

  it("等待服务端确认时给出可重试提示，而不是无声转圈", () => {
    render(
      <ActionStatusCard
        request={request({ status: "queued", awaitingAck: true })}
      />,
    );
    expect(screen.getByText(/正在查询原请求状态/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /重试（同一请求 ID）/ }),
    ).toBeInTheDocument();
  });

  it("已收尾的请求不再显示等待待办", () => {
    render(
      <ActionStatusCard
        request={request({
          status: "completed",
          awaiting: {
            kind: "move",
            note: "尚未出发",
            target: "",
            destinationSceneId: "morgue",
            disclosed: [],
            reason: "",
          },
        })}
      />,
    );
    expect(screen.queryByTestId("structured-awaiting")).toBeNull();
  });

  it("completed 与领域结果分开显示", () => {
    render(
      <ActionStatusCard
        request={request({
          status: "completed",
          outcome: "success",
          detail: "医生认出了签名。",
        })}
      />,
    );
    expect(screen.getByText("已处理完成")).toBeInTheDocument();
    expect(screen.getByText("结果：成功")).toBeInTheDocument();
    expect(screen.getByText("医生认出了签名。")).toBeInTheDocument();
  });

  it("completed 但领域结果失败时显示“结果：失败”", () => {
    render(
      <ActionStatusCard
        request={request({ status: "completed", outcome: "failure" })}
      />,
    );
    expect(screen.getByText("结果：失败")).toBeInTheDocument();
  });

  it("declined 显示拒绝原因", () => {
    render(
      <ActionStatusCard
        request={request({
          status: "declined",
          errorCode: "stale_target",
          errorMessage: "目标已失效（场景或状态已变化），请重新选择。",
        })}
      />,
    );
    expect(screen.getByText("已被拒绝")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("目标已失效");
  });

  it("revision 冲突时提供“用最新版本重新提交”", () => {
    render(
      <ActionStatusCard
        request={request({
          errorCode: "revision_conflict",
          errorMessage: "世界状态已更新，请刷新候选后重新提交。",
        })}
      />,
    );
    expect(screen.getByTestId("structured-resubmit")).toBeInTheDocument();
    expect(screen.getByTestId("structured-resubmit")).toHaveAttribute(
      "title",
      "世界已经前进：用最新版本号与新请求 ID 重新提交同一意图",
    );
  });

  it("超时未确认时提示“正在查询原请求状态”并提供同 ID 重试", () => {
    render(<ActionStatusCard request={request({ awaitingAck: true })} />);
    expect(screen.getByText(/正在查询原请求状态/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "重试（同一请求 ID）" }),
    ).toBeInTheDocument();
  });

  it("paused 显示可恢复，不显示终态", () => {
    render(
      <ActionStatusCard
        request={request({ status: "paused", detail: "守秘人离线" })}
      />,
    );
    expect(screen.getByText("已暂停（可恢复）")).toBeInTheDocument();
    expect(screen.getByText("守秘人离线")).toBeInTheDocument();
  });
});

describe("CheckRequestCard：参数来自服务端，只读与可响应区分", () => {
  it("展示技能/难度/奖惩骰/尝试/已知代价，参数不可编辑", () => {
    render(
      <CheckRequestCard
        check={check({ bonusPenalty: -1 })}
        canRespond
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByText(/检定请求 · 说服/)).toBeInTheDocument();
    expect(screen.getByText("常规")).toBeInTheDocument();
    expect(screen.getByText("惩罚骰 ×1")).toBeInTheDocument();
    expect(screen.getByText(/尝试：向医生说明来意/)).toBeInTheDocument();
    expect(screen.getByText(/已知代价：可能需要出示证件/)).toBeInTheDocument();
    // 没有任何可改难度/技能值的输入控件。
    expect(document.querySelectorAll("input, select")).toHaveLength(0);
  });

  it("指定玩家可以掷骰/放弃", () => {
    const onRespond = vi.fn();
    render(
      <CheckRequestCard check={check()} canRespond onRespond={onRespond} />,
    );
    screen.getByRole("button", { name: "掷骰" }).click();
    expect(onRespond).toHaveBeenCalledWith("roll");
    screen.getByRole("button", { name: "放弃" }).click();
    expect(onRespond).toHaveBeenCalledWith("decline");
  });

  it("其他玩家只读：按钮禁用并说明原因", () => {
    render(
      <CheckRequestCard
        check={check()}
        canRespond={false}
        onRespond={vi.fn()}
      />,
    );
    const roll = screen.getByRole("button", { name: "掷骰" });
    expect(roll).toBeDisabled();
    expect(roll).toHaveAttribute(
      "title",
      "这条检定由其他调查员响应，你只能查看",
    );
  });

  it("已结算显示服务端结果", () => {
    render(
      <CheckRequestCard
        check={check({
          status: "resolved",
          result: {
            targetValue: 55,
            roll: 23,
            outcome: "success",
            detail: "23 ≤ 55",
          },
        })}
        canRespond
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByText("已结算")).toBeInTheDocument();
    expect(screen.getByText(/23 vs 55/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "掷骰" }),
    ).not.toBeInTheDocument();
  });

  it("放弃后显示已放弃且不再可点", () => {
    render(
      <CheckRequestCard
        check={check({
          status: "declined",
          result: {
            targetValue: null,
            roll: null,
            outcome: "declined",
            detail: "你选择放弃。",
          },
        })}
        canRespond
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByText("已放弃")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "放弃" }),
    ).not.toBeInTheDocument();
  });
});

describe("StructuredDock", () => {
  it("没有任何待办时不渲染", () => {
    render(<StructuredDock />);
    expect(screen.queryByTestId("structured-dock")).not.toBeInTheDocument();
  });

  it("待检定只有指定玩家可点，其他玩家看到只读", () => {
    useStructuredStore.setState((state) => ({
      ...state,
      identity: {
        ...state.identity,
        worldId: WORLD_ID,
        investigatorId: "inv-alice",
      },
    }));
    useStructuredStore.getState().applyEvent({
      event_id: 5,
      world_id: WORLD_ID,
      revision: 12,
      type: "check_requested",
      payload: EVENT_FIXTURES.checkRequested.payload,
    });
    render(<StructuredDock />);
    expect(screen.getByRole("button", { name: "掷骰" })).toBeEnabled();
  });

  it("别人的待检定在本地只读", () => {
    useStructuredStore.setState((state) => ({
      ...state,
      identity: {
        ...state.identity,
        worldId: WORLD_ID,
        investigatorId: "inv-bob",
      },
    }));
    useStructuredStore.getState().applyEvent({
      event_id: 6,
      world_id: WORLD_ID,
      revision: 12,
      type: "check_requested",
      payload: EVENT_FIXTURES.checkRequested.payload,
    });
    render(<StructuredDock />);
    expect(screen.getByRole("button", { name: "掷骰" })).toBeDisabled();
  });

  it("协议版本不匹配时抽屉里给出明确提示", () => {
    useStructuredStore
      .getState()
      .setProtocolNotice("服务端使用不同版本的协议。");
    render(<StructuredDock />);
    expect(screen.getByRole("alert")).toHaveTextContent("不同版本的协议");
  });
});

describe("assisted 草稿与 agent 控制权（按能力渲染）", () => {
  it("服务端未声明 assisted 能力时不渲染草稿卡", () => {
    useStructuredStore.setState((state) => ({
      ...state,
      keeperDraft: {
        draftId: "d1",
        summary: "建议请求一次说服检定",
        command: { kind: "request_check", payload: { skill: "说服" } },
        note: "",
        createdAt: Date.now(),
      },
    }));
    render(<StructuredDock />);
    expect(screen.queryByTestId("keeper-draft-card")).not.toBeInTheDocument();
  });

  it("声明能力后草稿可见；批准与拒绝都走 resolve_draft", () => {
    const sent: Record<string, unknown>[] = [];
    setStructuredSender((payload) => {
      sent.push(payload as Record<string, unknown>);
      return true;
    });
    useStructuredStore.getState().applyCapabilities({
      ...STRUCTURED_CAPABILITIES_WIRE,
      assisted_draft: true,
    });
    useStructuredStore
      .getState()
      .applySnapshot(
        EVENT_FIXTURES.snapshot.payload as Record<string, unknown>,
        WORLD_ID,
      );
    useStructuredStore.setState((state) => ({
      ...state,
      keeperDraft: {
        draftId: "d1",
        summary: "建议请求一次说服检定",
        command: { kind: "request_check", payload: { skill: "说服" } },
        note: "",
        createdAt: Date.now(),
      },
    }));
    render(<StructuredDock />);
    expect(screen.getByTestId("keeper-draft-card")).toBeInTheDocument();
    // 批准 → resolve_draft{approved}（由服务端按草稿执行，前端不再直接发该命令）。
    screen.getByTestId("draft-approve").click();
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      type: "command_request",
      kind: "resolve_draft",
      payload: { draft_id: "d1", decision: "approved" },
    });
    // 拒绝 → resolve_draft{rejected}，不是本地丢弃。
    sent.length = 0;
    // 换一张草稿要进 act：否则点击可能发生在旧 DOM 上（draft_id 仍是 d1）。
    act(() => {
      useStructuredStore.setState((state) => ({
        ...state,
        keeperDraft: {
          draftId: "d2",
          summary: "建议再次检定",
          command: { kind: "request_check", payload: { skill: "侦查" } },
          note: "",
          createdAt: Date.now(),
        },
      }));
    });
    screen.getByTestId("draft-reject").click();
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      kind: "resolve_draft",
      payload: { draft_id: "d2", decision: "rejected" },
    });
  });

  it("agent 暂停/超预算显示状态，未开放接管时写明", () => {
    useStructuredStore.getState().applyEvent({
      event_id: 30,
      world_id: WORLD_ID,
      revision: 12,
      type: "keeper_control",
      payload: {
        state: "budget_exceeded",
        detail: "已用尽本轮预算",
        takeover_available: false,
      },
    });
    render(<StructuredDock />);
    const notice = screen.getByTestId("keeper-control-notice");
    expect(notice).toHaveTextContent("Agent 已超预算，等待接管");
    expect(notice).toHaveTextContent("已用尽本轮预算");
    expect(notice).toHaveTextContent("服务端未开放接管入口");
  });
});
