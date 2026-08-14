import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { claimByKey, enterSoloLobby, startGame } from "../../../online";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { useStartStore } from "../../../state/start-store";
import { SoloCharacterSelectScreen } from "./SoloCharacterSelectScreen";

vi.mock("../../../online", () => ({
  claimByKey: vi.fn(),
  enterSoloLobby: vi.fn(),
  startGame: vi.fn(),
}));

const fullCharacter = {
  ref: { source: "module", id: "emily" },
  id: "default:alice",
  name: "艾米莉",
  occupation: "记者",
  era: "1920年代",
  source_label: "猩红文档",
  hp: 11,
  max_hp: 11,
  san: 55,
  max_san: 55,
  reputation: 0,
  completed_modules: 0,
  attributes: { STR: 50, CON: 60, DEX: 70 },
  top_skills: [{ id: "spot_hidden", value: 60 }],
  backstory: { background: "常年奔波于罪案现场。" },
};

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
  useStartStore.setState({
    characterGroups: [],
    activeModuleTitle: "猩红文档",
  });
  vi.clearAllMocks();
});

describe("SoloCharacterSelectScreen", () => {
  it("展示角色卡并把选择提交给房间接口（点卡即认领）", () => {
    render(<SoloCharacterSelectScreen />);
    expect(screen.getByText("雾中宅邸")).toBeInTheDocument();
    expect(screen.getAllByText("艾米莉").length).toBeGreaterThan(0);
    expect(screen.getAllByText("记者").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /艾米莉/ }));
    expect(claimByKey).toHaveBeenCalledWith("default:alice");
  });

  it("认领后允许「以此调查员开始」，并可返回冒险列表", () => {
    render(<SoloCharacterSelectScreen />);
    expect(
      screen.getByRole("button", { name: "以此调查员开始" }),
    ).toBeDisabled();

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
    fireEvent.click(screen.getByRole("button", { name: "以此调查员开始" }));
    fireEvent.click(screen.getByRole("button", { name: /返回我的冒险/ }));
    expect(startGame).toHaveBeenCalledTimes(1);
    expect(enterSoloLobby).toHaveBeenCalledTimes(1);
  });

  it("character_list 有完整档案时用共享档案卡呈现属性/技能/背景", () => {
    useStartStore.setState({
      characterGroups: [
        { id: "module", title: "模组调查员", characters: [fullCharacter] },
      ],
    });
    render(<SoloCharacterSelectScreen />);
    const dossier = document.querySelector(".character-dossier");
    expect(dossier).not.toBeNull();
    expect(dossier!.textContent).toContain("基础属性");
    expect(dossier!.textContent).toContain("擅长技能");
    expect(dossier!.textContent).toContain("常年奔波于罪案现场。");
  });

  it("首组为空（房间模式无个人角色）时仍聚焦第一个可用角色并渲染档案卡", () => {
    useStartStore.setState({
      characterGroups: [
        { id: "profile", title: "长期角色", characters: [] },
        { id: "module", title: "模组调查员", characters: [fullCharacter] },
        { id: "custom", title: "自定义角色", characters: [] },
      ],
    });
    render(<SoloCharacterSelectScreen />);
    const dossier = document.querySelector(".character-dossier");
    expect(dossier).not.toBeNull();
    expect(dossier!.textContent).toContain("艾米莉");
  });

  it("被其他成员认领的角色卡禁用并标注已被占用", () => {
    act(() => {
      useOnlineStore.setState({
        members: [
          {
            user_id: "u1",
            username: "alice",
            role: "owner",
            investigator: null,
          },
          {
            user_id: "u2",
            username: "bob",
            role: "player",
            investigator: {
              id: "investigator-2",
              character_key: "default:bob",
            },
          },
        ],
      });
    });
    render(<SoloCharacterSelectScreen />);
    const bobCard = screen.getByRole("button", { name: /罗伯特/ });
    expect(bobCard).toBeDisabled();
    expect(bobCard.textContent).toContain("已被占用");
  });
});
