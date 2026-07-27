import { beforeEach, describe, expect, it } from "vitest";

import { sendAction } from "./options";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import { useStartStore } from "./state/start-store";
import { handleServerPayload } from "./ws";

beforeEach(() => {
  useMessageStore.setState({ messages: [] });
  useStartStore.setState({ gameStarted: false, gameStarting: false });
  useAppStore.setState({
    mode: "online",
    inputEnabled: true,
    inputPlaceholder: "你决定做什么？",
    choices: [],
    dialog: null,
    ending: null,
  });
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: { id: "u1", username: "alice" },
    roomConnection: "connected",
    roomStatus: "playing",
    currentActorUserId: "u1",
    members: [{ user_id: "u1", username: "alice", role: "player" }],
  });
});

describe("gm_turn_start 玩家行动广播", () => {
  it("发送者用权威 actor 升级乐观气泡，不产生重复", () => {
    sendAction("检查书桌");
    handleServerPayload({
      type: "gm_turn_start",
      turn_id: "turn-local-authority",
      seq: 0,
      player_input: "检查书桌",
      actor: {
        type: "investigator",
        user_id: "u1",
        investigator_id: "inv-1",
        name: "黄千陆",
      },
    });

    const players = useMessageStore
      .getState()
      .messages.filter((message) => message.kind === "player");
    expect(players).toHaveLength(1);
    expect(players[0]).toMatchObject({
      turnId: "turn-local-authority",
      speaker: { name: "黄千陆", userId: "u1" },
    });
  });

  it("其他成员收到开局事件后立即看到行动者气泡", () => {
    useOnlineStore.setState({
      currentActorUserId: "u2",
      members: [
        { user_id: "u1", username: "alice", role: "player" },
        { user_id: "u2", username: "bob", role: "player" },
      ],
    });

    handleServerPayload({
      type: "gm_turn_start",
      turn_id: "turn-remote-authority",
      seq: 0,
      player_input: "翻阅档案",
      actor: {
        type: "investigator",
        user_id: "u2",
        investigator_id: "inv-2",
        name: "温蒂",
      },
    });

    expect(
      useMessageStore
        .getState()
        .messages.find((message) => message.kind === "player"),
    ).toMatchObject({
      text: "翻阅档案",
      turnId: "turn-remote-authority",
      speaker: { name: "温蒂", userId: "u2" },
    });
  });
});
