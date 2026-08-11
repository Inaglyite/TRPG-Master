import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { claimByKey, enterSoloLobby, startGame } from "../../../online";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { SoloCharacterSelectScreen } from "./SoloCharacterSelectScreen";

vi.mock("../../../online", () => ({
  claimByKey: vi.fn(),
  enterSoloLobby: vi.fn(),
  startGame: vi.fn(),
}));

beforeEach(() => {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: { id: "u1", username: "alice" },
    roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
    members: [
      {
        user_id: "u1",
        username: "alice",
        role: "owner",
        investigator: null,
      },
    ],
    characterOptions: [
      {
        id: "default:alice",
        name: "艾米莉",
        occupation: "记者",
      },
      { id: "default:bob", name: "罗伯特", occupation: "医生" },
    ],
    charactersStatus: "ready",
    roomConnection: "connected",
  });
  vi.clearAllMocks();
});

describe("SoloCharacterSelectScreen", () => {
  it("展示角色卡并把选择提交给房间接口", () => {
    render(<SoloCharacterSelectScreen />);
    expect(screen.getByText("雾中宅邸")).toBeInTheDocument();
    expect(screen.getByText("艾米莉")).toBeInTheDocument();
    expect(screen.getByText("记者")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: /选择角色/ })[0]);
    expect(claimByKey).toHaveBeenCalledWith("default:alice");
  });

  it("选定角色后允许开始调查，并可返回冒险列表", () => {
    const { rerender } = render(<SoloCharacterSelectScreen />);
    expect(screen.getByRole("button", { name: "开始调查" })).toBeDisabled();

    act(() => {
      useOnlineStore.setState({
        members: [
          {
            user_id: "u1",
            username: "alice",
            role: "owner",
            investigator: {
              id: "investigator-1",
              character_key: "default:alice",
            },
          },
        ],
      });
    });
    rerender(<SoloCharacterSelectScreen />);
    fireEvent.click(screen.getByRole("button", { name: "开始调查" }));
    fireEvent.click(screen.getByRole("button", { name: /返回我的冒险/ }));
    expect(startGame).toHaveBeenCalledTimes(1);
    expect(enterSoloLobby).toHaveBeenCalledTimes(1);
  });
});
