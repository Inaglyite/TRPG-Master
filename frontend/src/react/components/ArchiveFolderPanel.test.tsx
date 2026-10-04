import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArchiveFolderPanel } from "./ArchiveFolderPanel";

describe("ArchiveFolderPanel", () => {
  it("retains accessible live content rather than baking controls into a bitmap", () => {
    const action = vi.fn();
    render(
      <ArchiveFolderPanel aria-label="恢复连接" className="test-panel">
        <h2>无法连接服务器</h2>
        <button onClick={action}>重新检查</button>
      </ArchiveFolderPanel>,
    );
    expect(screen.getByRole("region", { name: "恢复连接" })).toHaveClass(
      "archive-folder-panel--portrait",
      "test-panel",
    );
    screen.getByRole("button", { name: "重新检查" }).click();
    expect(action).toHaveBeenCalledTimes(1);
  });

  it("selects the horizontal composition without changing child semantics", () => {
    render(
      <ArchiveFolderPanel variant="wide" aria-label="当前服务器">
        <p>https://table.example.com</p>
      </ArchiveFolderPanel>,
    );
    expect(screen.getByRole("region", { name: "当前服务器" })).toHaveClass(
      "archive-folder-panel--wide",
    );
    expect(screen.getByText("https://table.example.com")).toBeInTheDocument();
  });
});
