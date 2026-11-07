import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { ModuleManager } from "./ModuleManager";
import { deleteModule, listManagedModules } from "../../api/moduleManagement";
vi.mock("../../api/moduleManagement", () => ({
  deleteModule: vi.fn(),
  listManagedModules: vi.fn(),
}));
const imported = {
  id: "test@1",
  title: "夜波测试",
  source: "user",
  version: "1",
  world_count: 3,
  save_count: 5,
  delete_block: "",
};
describe("ModuleManager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({ mode: "local" });
    vi.mocked(listManagedModules).mockResolvedValue({
      modules: [
        imported,
        {
          ...imported,
          id: "builtin",
          title: "内置测试",
          source: "builtin",
          delete_block: "内置模组不能删除",
        },
      ],
    });
  });
  it("requires confirmation with save counts, updates selection list after removal", async () => {
    vi.mocked(deleteModule).mockResolvedValue({
      ok: true,
      world_count: 3,
      save_count: 5,
      cleanup_pending: false,
    });
    render(<ModuleManager />);
    fireEvent.click(screen.getByRole("button", { name: "模组管理" }));
    const remove = await screen.findByRole("button", { name: "删除夜波测试" });
    await waitFor(() => expect(remove).toBeEnabled());
    expect(screen.getByRole("button", { name: "删除内置测试" })).toBeDisabled();
    fireEvent.click(remove);
    expect(deleteModule).not.toHaveBeenCalled();
    expect(
      screen.getByText(/将永久删除该版本的模组文件、3 条时间线及 5 个存档点/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteModule).not.toHaveBeenCalled();
    fireEvent.click(remove);
    fireEvent.click(screen.getByRole("button", { name: "确认删除模组及存档" }));
    await waitFor(() => expect(deleteModule).toHaveBeenCalledWith("test@1"));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "删除夜波测试" }),
      ).not.toBeInTheDocument(),
    );
    expect(useStartStore.getState().modules.map((m) => m.id)).toEqual([
      "builtin",
    ]);
  });
  it("keeps the entry and exposes failure for retry", async () => {
    vi.mocked(deleteModule).mockRejectedValue(new Error("模组正在使用"));
    render(<ModuleManager />);
    fireEvent.click(screen.getByRole("button", { name: "模组管理" }));
    const remove = await screen.findByRole("button", { name: "删除夜波测试" });
    await waitFor(() => expect(remove).toBeEnabled());
    fireEvent.click(remove);
    fireEvent.click(screen.getByRole("button", { name: "确认删除模组及存档" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("模组正在使用");
    expect(remove).toBeInTheDocument();
  });
  it("hides local management in cloud mode", () => {
    useAppStore.setState({ mode: "online" });
    render(<ModuleManager />);
    expect(
      screen.queryByRole("button", { name: "模组管理" }),
    ).not.toBeInTheDocument();
    expect(listManagedModules).not.toHaveBeenCalled();
  });
});
