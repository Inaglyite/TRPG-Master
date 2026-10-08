import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CaseCharacterActions } from "./CaseCharacterActions";
import {
  previewCaseCharacter,
  saveCaseCharacter,
  exportCaseCharacter,
  libraryEntrySchema,
} from "../../../api/characterLibrary";
import {
  useStructuredStore,
  initialStructuredState,
} from "../../../state/structured-store";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import type { CaseSettlement } from "../../../protocol/combat";
vi.mock("../../../api/characterLibrary", async () => ({
  ...(await vi.importActual("../../../api/characterLibrary")),
  previewCaseCharacter: vi.fn(),
  saveCaseCharacter: vi.fn(),
  exportCaseCharacter: vi.fn(),
}));
const receipt: CaseSettlement = {
  investigator_id: "alice",
  character_id: "char-a",
  case: {
    case_id: "case-a",
    world_id: "world-a",
    ending_type: "good",
    reputation_delta: 2,
  },
  career: { reputation: 12, case_history: [], completed_modules: [] },
};
const ready = {
  ok: true,
  card: { name: "爱丽丝" },
  warnings: [],
  revision: 7,
  receipt_digest: "a".repeat(64),
  saved_entry: null,
};
beforeEach(() => {
  vi.clearAllMocks();
  useStructuredStore.setState({
    ...initialStructuredState,
    identity: {
      ...initialStructuredState.identity,
      worldId: "world-a",
      investigatorId: "alice",
      revision: 7,
    },
  });
  useAppStore.setState({ mode: "local" });
  useOnlineStore.setState({ user: null });
  vi.mocked(previewCaseCharacter).mockResolvedValue(ready);
});
describe("结案角色：明确保存、不污染后续上下文", () => {
  it("打开只有只读预览，保存使用服务端版本与凭证，不重复创建", async () => {
    const entry = libraryEntrySchema.parse({ id: "saved-a", name: "新爱丽丝" });
    vi.mocked(saveCaseCharacter).mockResolvedValue({
      ok: true,
      entry,
      warnings: [],
      deduplicated: false,
    });
    render(<CaseCharacterActions receipt={receipt} />);
    await waitFor(() =>
      expect(screen.getByLabelText("新角色名")).toHaveValue("爱丽丝"),
    );
    expect(saveCaseCharacter).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("新角色名"), {
      target: { value: "新爱丽丝" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存为新角色" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "已保存" })).toBeDisabled(),
    );
    expect(saveCaseCharacter).toHaveBeenCalledWith(
      expect.objectContaining({
        world_id: "world-a",
        investigator_id: "alice",
        expected_revision: 7,
        receipt_digest: "a".repeat(64),
        name: "新爱丽丝",
      }),
    );
    expect(saveCaseCharacter).toHaveBeenCalledTimes(1);
  });
  it("保存失败保留名字并允许重新查看，不谎报已保存", async () => {
    vi.mocked(saveCaseCharacter).mockRejectedValue(new Error("世界版本已变化"));
    render(<CaseCharacterActions receipt={receipt} />);
    await screen.findByLabelText("新角色名");
    fireEvent.change(screen.getByLabelText("新角色名"), {
      target: { value: "我的副本" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存为新角色" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "已保存" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新查看" }));
    await waitFor(() =>
      expect(screen.getByLabelText("新角色名")).toHaveValue("我的副本"),
    );
  });
  it("迟到的预览不能覆盖换世界后的角色名", async () => {
    let complete!: (value: typeof ready) => void;
    vi.mocked(previewCaseCharacter).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    vi.mocked(previewCaseCharacter).mockResolvedValueOnce({
      ...ready,
      card: { name: "新的角色" },
    });
    render(<CaseCharacterActions receipt={receipt} />);
    act(() =>
      useStructuredStore.setState((s) => ({
        identity: { ...s.identity, worldId: "world-b" },
      })),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("新角色名")).toHaveValue("新的角色"),
    );
    await act(async () => complete({ ...ready, card: { name: "旧角色" } }));
    expect(screen.getByLabelText("新角色名")).toHaveValue("新的角色");
  });
  it("导出是显式动作且不会创建角色库条目", async () => {
    vi.mocked(exportCaseCharacter).mockResolvedValue(undefined);
    render(<CaseCharacterActions receipt={receipt} />);
    await screen.findByLabelText("新角色名");
    fireEvent.click(screen.getByRole("button", { name: "导出角色卡" }));
    await waitFor(() => expect(exportCaseCharacter).toHaveBeenCalledTimes(1));
    expect(saveCaseCharacter).not.toHaveBeenCalled();
  });
});
