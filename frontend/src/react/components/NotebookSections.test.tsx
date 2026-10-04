import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { NotebookSections } from "./NotebookSections";

describe("NotebookSections", () => {
  it("笔记先于快捷入口，默认不展开行动", () => {
    const submit = vi.fn();
    render(
      <NotebookSections
        open
        notes={<textarea aria-label="私人笔记" />}
        actions={<button onClick={submit}>观察环境</button>}
      />,
    );
    const notes = screen.getByRole("textbox", { name: "私人笔记" });
    const toggle = screen.getByRole("button", { name: "快捷行动" });
    expect(
      notes.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("button", { name: "观察环境" }),
    ).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "观察环境" })).toBeVisible();
    expect(submit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "观察环境" }));
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it("展开和收起不重建或丢失笔记草稿", () => {
    function Draft() {
      const [text, setText] = useState("");
      return (
        <textarea
          aria-label="私人笔记"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      );
    }
    render(
      <NotebookSections open notes={<Draft />} actions={<p>次级操作</p>} />,
    );
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "只有我知道的线索" } });
    const toggle = screen.getByRole("button", { name: "快捷行动" });
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(screen.getByRole("textbox")).toBe(input);
    expect(input).toHaveValue("只有我知道的线索");
  });

  it("重新打开回到笔记，保留操作禁用和原有草稿", () => {
    const props = {
      notes: <textarea aria-label="私人笔记" defaultValue="保留草稿" />,
      actions: <button disabled>观察环境</button>,
    };
    const { rerender } = render(<NotebookSections open {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "快捷行动" }));
    expect(screen.getByRole("button", { name: "观察环境" })).toBeDisabled();
    rerender(<NotebookSections open={false} {...props} />);
    rerender(<NotebookSections open {...props} />);
    expect(screen.getByRole("button", { name: "快捷行动" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByRole("textbox")).toHaveValue("保留草稿");
  });
});
