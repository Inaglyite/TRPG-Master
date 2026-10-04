import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClueImagePreview } from "./ClueImagePreview";

const source = "data:image/png;base64,AA==";
const selection = { source, label: "授权图片", scope: "world-a" };

describe("unwired clue reading surface", () => {
  it("renders the shared accessible viewer and close only calls its UI callback", () => {
    const onClose = vi.fn();
    render(
      <ClueImagePreview
        selection={selection}
        scope="world-a"
        authorizedSources={[source]}
        returnFocus={{ current: null }}
        onClose={onClose}
      />,
    );
    expect(
      screen.getByRole("dialog", { name: "授权图片" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭材料查看" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it("does not render old scope images and discards selection", () => {
    const onClose = vi.fn();
    render(
      <ClueImagePreview
        selection={selection}
        scope="world-b"
        authorizedSources={[source]}
        returnFocus={{ current: null }}
        onClose={onClose}
      />,
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it("revocation hides the existing viewer, without waiting for a closing animation", () => {
    const onClose = vi.fn();
    const returnFocus = { current: null };
    const { rerender } = render(
      <ClueImagePreview
        selection={selection}
        scope="world-a"
        authorizedSources={[source]}
        returnFocus={returnFocus}
        onClose={onClose}
      />,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    rerender(
      <ClueImagePreview
        selection={selection}
        scope="world-a"
        authorizedSources={[]}
        returnFocus={returnFocus}
        onClose={onClose}
      />,
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
