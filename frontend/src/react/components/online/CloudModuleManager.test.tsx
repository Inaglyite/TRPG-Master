import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../../../api/client";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { CloudModuleManager } from "./CloudModuleManager";

vi.mock("../../../api/client", () => ({
  apiFetch: vi.fn(),
}));

describe("CloudModuleManager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useOnlineStore.setState({
      ...initialOnlineState,
      modulesStatus: "ready",
      modules: [
        { id: "server-a", title: "模组甲", source: "builtin" },
        {
          id: "personal-b",
          title: "模组乙",
          source: "account",
          world_count: 2,
          save_count: 1,
        },
        { id: "server-c", title: "模组丙", source: "server" },
      ],
    });
    vi.mocked(apiFetch).mockResolvedValue({ ok: true });
  });

  it("只提供排序和个人模组移除，不提供模组选择", async () => {
    render(<CloudModuleManager onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "模组管理" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "将模组甲下移" }),
      ).toBeEnabled(),
    );

    expect(screen.getByRole("button", { name: "将模组甲下移" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "将模组甲上移" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "将模组丙下移" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "从模组库移除模组乙" }));
    const confirmation = screen.getByRole("region", {
      name: "确认从模组库移除",
    });
    expect(confirmation).toHaveTextContent("永久删除");
    expect(confirmation).toHaveTextContent("2 条时间线");
    expect(confirmation).toHaveTextContent("1 个存档点");
    expect(confirmation).toHaveTextContent("重新导入不会恢复");
    expect(confirmation).not.toHaveTextContent("已有冒险仍可从存档继续");
    expect(
      screen.queryByRole("button", { name: "选择模组甲" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "已选择" })).toBeNull();
  });

  it("移除后显示实际级联删除计数", async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    vi.mocked(apiFetch).mockResolvedValue({
      ok: true,
      world_count: 2,
      save_count: 1,
      cleanup_pending: false,
    });
    render(<CloudModuleManager onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: "模组管理" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "将模组甲下移" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "从模组库移除模组乙" }));
    fireEvent.click(screen.getByRole("button", { name: "确认从库中移除" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("已删除「模组乙」"),
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("2 条时间线");
    expect(status).toHaveTextContent("1 个存档点");
    expect(status).toHaveTextContent("重新导入不会恢复");
  });

  it("下移后提交完整显示顺序并刷新账号模组列表", async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    render(<CloudModuleManager onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: "模组管理" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "将模组甲下移" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "将模组甲下移" }));

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        "/api/me/modules/order",
        expect.anything(),
        {
          method: "PUT",
          body: { module_ids: ["personal-b", "server-a", "server-c"] },
        },
      ),
    );
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(screen.getByRole("status")).toHaveTextContent(
      "已将「模组甲」下移，当前第 2 位",
    );
  });
});
