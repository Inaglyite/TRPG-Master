import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KeeperLibrary } from "./KeeperLibrary";
import {
  useStructuredStore,
  initialStructuredState,
} from "../../../state/structured-store";
import { useAppStore } from "../../../state/app-store";
import {
  loadKeeperGuide,
  loadStructuredAsset,
} from "../../../api/structuredAssets";
import { sendKeeperCommand } from "../../../structured-transport";

vi.mock("../../../api/structuredAssets", () => ({
  loadStructuredAsset: vi.fn(),
  loadKeeperGuide: vi.fn(),
}));
const readGuide = vi.mocked(loadKeeperGuide);
vi.mock("../../../structured-transport", () => ({
  sendKeeperCommand: vi.fn(),
}));
const readImage = vi.mocked(loadStructuredAsset);
const send = vi.mocked(sendKeeperCommand);
const uri = "data:image/png;base64,aW1hZ2U=";
const onPrepare = vi.fn();

function openLibrary() {
  render(
    <KeeperLibrary
      investigators={[
        { id: "alice", name: "爱丽丝" },
        { id: "bob", name: "鲍勃" },
      ]}
      blocked={null}
      onPrepare={onPrepare}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /打开资料库/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ mode: "online" });
  useStructuredStore.setState({
    ...initialStructuredState,
    identity: { ...initialStructuredState.identity, worldId: "world-1" },
    capabilities: {
      ...initialStructuredState.capabilities,
      commands: ["present_handout", "grant_clue"],
    },
    keeperMaterial: [
      {
        id: "study",
        kind: "scene",
        current: true,
        title: "旧书房",
        text: "没有揭示的密室。",
      },
      {
        id: "npc",
        kind: "npc",
        title: "看守",
        text: "主持秘密：钥匙藏在暗格。",
      },
    ],
    keeperAssets: [{ id: "office-photo", label: "书房照片" }],
    clues: [
      {
        id: "clue",
        text: "医生的私人笔记",
        category: "investigation",
        presentation: ["describe"],
        allowedPhysicalItemIds: [],
      },
    ],
  });
  readImage.mockResolvedValue({
    asset_id: "office-photo",
    label: "书房照片",
    asset_data_uri: uri,
  });
  send.mockReturnValue({ ok: true, requestId: "cmd-1", payload: {} });
  readGuide.mockResolvedValue({
    module_title: "案卷",
    source_version: "v1",
    warnings: [],
    documents: [
      {
        id: "module",
        title: "模组正文",
        text: "作者的完整主持手册。<script>不会执行</script>",
      },
    ],
  });
});

it("the party archive shows full sheets and only prepares explicit check/item forms", () => {
  useStructuredStore.getState().applySnapshot(
    {
      keeper_investigators: [
        {
          investigator_id: "alice",
          name: "爱丽丝",
          occupation: "记者",
          hp: 9,
          max_hp: 12,
          san: 60,
          max_san: 80,
          skills: { rare_skill: 17, occult: 23 },
          attributes: { INT: 70 },
          inventory: [{ id: "bandage", label: "绷带", quantity: 2 }],
        },
      ],
    },
    "world-1",
  );
  useStructuredStore.setState({
    capabilities: {
      ...initialStructuredState.capabilities,
      commands: ["request_check", "use_item"],
    },
  });
  openLibrary();
  fireEvent.click(screen.getByRole("button", { name: "队员" }));
  expect(screen.getByText("HP 9/12")).toBeInTheDocument();
  expect(screen.getByText("rare_skill")).toBeInTheDocument();
  expect(screen.getByText("神秘学")).toBeInTheDocument();
  expect(screen.getByText("智力")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "准备 rare_skill 检定" }));
  expect(onPrepare).toHaveBeenCalledWith("request_check", {
    investigator_id: "alice",
    skill: "rare_skill",
  });
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /打开资料库/ }));
  fireEvent.click(screen.getByRole("button", { name: "准备使用 绷带" }));
  expect(onPrepare).toHaveBeenCalledWith("use_item", {
    investigator_id: "alice",
    item_id: "bandage",
    quantity: 1,
    consume: false,
  });
  expect(send).not.toHaveBeenCalled();
});

describe("KeeperLibrary", () => {
  it("loads author documents only on manual selection, as inert searchable text", async () => {
    openLibrary();
    expect(readGuide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "模组手册" }));
    await screen.findByText("模组正文", { selector: "h5" });
    expect(readGuide).toHaveBeenCalledWith("world-1", false);
    expect(screen.getByRole("article")).toHaveTextContent(
      "<script>不会执行</script>",
    );
    expect(document.querySelector(".keeper-library-preview script")).toBeNull();
    expect(screen.getByText(/不是存档内的历史快照/)).toBeVisible();
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "完整主持" },
    });
    expect(screen.getByRole("article")).toHaveTextContent("作者的完整主持手册");
    expect(send).not.toHaveBeenCalled();
  });

  it("manual failures are actionable and a late old-world manual is discarded", async () => {
    readGuide.mockRejectedValueOnce(new Error("资料暂不可用"));
    openLibrary();
    fireEvent.click(screen.getByRole("button", { name: "模组手册" }));
    await screen.findByText("资料暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "重新读取资料" }));
    await screen.findByText("模组正文", { selector: "h5" });
    let done!: (value: Awaited<ReturnType<typeof loadKeeperGuide>>) => void;
    readGuide.mockReturnValue(
      new Promise((resolve) => {
        done = resolve;
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /收起资料库/ }));
    fireEvent.click(screen.getByRole("button", { name: /打开资料库/ }));
    act(() => useStructuredStore.getState().bindWorld("world-2", 1));
    await act(async () =>
      done({
        module_title: "旧世界",
        source_version: "1",
        warnings: [],
        documents: [{ id: "secret", title: "旧秘密", text: "不会留下" }],
      }),
    );
    expect(screen.queryByText("旧秘密")).not.toBeInTheDocument();
    expect(send).not.toHaveBeenCalled();
  });
  it("shows authored text only inside the opened reference library, with search and categories", () => {
    openLibrary();
    expect(screen.getByRole("article")).toHaveTextContent("没有揭示的密室。");
    fireEvent.click(screen.getByRole("button", { name: "人物" }));
    expect(screen.getByRole("article")).toHaveTextContent(
      "主持秘密：钥匙藏在暗格。",
    );
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "不存在" },
    });
    expect(screen.getByRole("navigation")).toHaveTextContent("没有匹配的资料");
    expect(readImage).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });

  it("preview never grants; explicit recipients and server confirmation are required", async () => {
    openLibrary();
    fireEvent.click(screen.getByRole("button", { name: "图片" }));
    await screen.findByRole("img", { name: "书房照片" });
    expect(readImage).toHaveBeenCalledWith("world-1", "office-photo", false);
    const button = screen.getByRole("button", { name: "展示给所选调查员" });
    expect(button).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "爱丽丝" }));
    fireEvent.click(button);
    expect(send).toHaveBeenCalledWith("present_handout", {
      asset_id: "office-photo",
      recipient_investigator_ids: ["alice"],
      caption: "书房照片",
    });
    expect(
      screen.queryByText("服务端已确认图片分发。"),
    ).not.toBeInTheDocument();
  });

  it("a clue selection only prepares a command; it never awards it automatically", () => {
    openLibrary();
    fireEvent.click(screen.getByRole("button", { name: "线索" }));
    fireEvent.click(screen.getByRole("button", { name: "准备分发这条线索" }));
    expect(onPrepare).toHaveBeenCalledWith("grant_clue", { clue_id: "clue" });
    expect(send).not.toHaveBeenCalled();
  });

  it("failed image access is visible and cannot be used as a successful preview", async () => {
    readImage.mockRejectedValue(new Error("图片不可用，或你尚未获准查看"));
    openLibrary();
    fireEvent.click(screen.getByRole("button", { name: "图片" }));
    await screen.findByText("图片不可用，或你尚未获准查看");
    fireEvent.click(screen.getByRole("checkbox", { name: "爱丽丝" }));
    expect(
      screen.getByRole("button", { name: "展示给所选调查员" }),
    ).toBeDisabled();
    readImage.mockResolvedValue({
      asset_id: "office-photo",
      label: "书房照片",
      asset_data_uri: uri,
    });
    fireEvent.click(screen.getByRole("button", { name: "重新读取图片" }));
    await screen.findByRole("img", { name: "书房照片" });
    expect(
      screen.getByRole("button", { name: "展示给所选调查员" }),
    ).toBeEnabled();
    expect(send).not.toHaveBeenCalled();
  });

  it("the current-scene mark follows committed state, not stale reference metadata", () => {
    openLibrary();
    expect(screen.getByRole("navigation")).toHaveTextContent("当前场景");
    act(() => useStructuredStore.setState({ currentSceneId: "another-scene" }));
    expect(screen.getByRole("navigation")).not.toHaveTextContent("当前场景");
  });

  it("changing worlds discards old private images and ignores a late response", async () => {
    let resolve!: (
      result: Awaited<ReturnType<typeof loadStructuredAsset>>,
    ) => void;
    readImage.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    openLibrary();
    fireEvent.click(screen.getByRole("button", { name: "图片" }));
    await waitFor(() => expect(readImage).toHaveBeenCalled());
    act(() => useStructuredStore.getState().bindWorld("world-2", 1));
    await act(async () =>
      resolve({
        asset_id: "office-photo",
        label: "旧图片",
        asset_data_uri: uri,
      }),
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /打开资料库/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("a player snapshot erases previous private references even for the same world", () => {
    act(() =>
      useStructuredStore
        .getState()
        .applySnapshot({ revision: 2, keeper: null }, "world-1"),
    );
    expect(useStructuredStore.getState().keeperMaterial).toEqual([]);
    expect(useStructuredStore.getState().keeperAssets).toEqual([]);
  });
});
