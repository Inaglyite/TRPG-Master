import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { AdventureArchiveConfirmation } from "./AdventureArchiveConfirmation";

it("归档抽屉关闭动画期间不再接受确认或取消，重新打开恢复操作", () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const props = { title: "测试冒险", busy: false, onConfirm, onCancel };
  const { rerender } = render(
    <AdventureArchiveConfirmation {...props} phase="open" />,
  );
  rerender(<AdventureArchiveConfirmation {...props} phase="closing" />);
  const confirm = screen.getByRole("button", { name: "确认归档" });
  const cancel = screen.getByRole("button", { name: "继续保留" });
  expect(confirm).toBeDisabled();
  expect(cancel).toBeDisabled();
  fireEvent.click(confirm);
  fireEvent.click(cancel);
  fireEvent.keyDown(confirm, { key: "Escape" });
  expect(onConfirm).not.toHaveBeenCalled();
  expect(onCancel).not.toHaveBeenCalled();
  rerender(<AdventureArchiveConfirmation {...props} phase="open" />);
  expect(confirm).toBeEnabled();
  expect(cancel).toHaveFocus();
  fireEvent.click(confirm);
  expect(onConfirm).toHaveBeenCalledOnce();
});

it("不使用动画的既有确认弹窗仍可操作，忙碌时拒绝确认与Escape", () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const props = { title: "测试冒险", busy: false, onConfirm, onCancel };
  const { rerender } = render(<AdventureArchiveConfirmation {...props} />);
  const confirm = screen.getByRole("button", { name: "确认归档" });
  expect(confirm).toBeEnabled();
  rerender(<AdventureArchiveConfirmation {...props} busy />);
  expect(confirm).toBeDisabled();
  fireEvent.click(confirm);
  fireEvent.keyDown(confirm, { key: "Escape" });
  expect(onConfirm).not.toHaveBeenCalled();
  expect(onCancel).not.toHaveBeenCalled();
});
