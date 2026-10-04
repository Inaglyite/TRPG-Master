import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../ws", () => ({ safeSend: vi.fn() }));
import { safeSend } from "../../ws";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { ModuleImporter } from "./ModuleImporter";

const summary = {
  module_key: "test",
  package_id: "test-package",
  version: "1.0.0",
  title: "测试模组",
  author: "测试作者",
  description: "仅测试的介绍。",
  system: "coc7",
  capabilities: [],
  file_count: 4,
  warnings: [],
};
function reply(value: unknown) {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}
function choose(container: HTMLElement) {
  fireEvent.change(container.querySelector("input[type=file]")!, {
    target: { files: [new File(["fixture"], "test.trpgmod")] },
  });
}
async function readyImportButton() {
  const button = await screen.findByRole("button", { name: "导入并切换" });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
}
describe("ModuleImporter boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({ mode: "local", connection: "connected" });
    useStartStore.setState({ gameStarted: false });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("focuses the dialog during inspection and exposes a named close control", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(reply({ ok: true, module: summary })),
    );
    const { container } = render(<ModuleImporter />);
    choose(container);
    await screen.findByText("测试模组");
    const close = screen.getByRole("button", { name: "关闭模组导入" });
    // The summary can render before inspect's finally clears busy. Assert the
    // actual interactive transition rather than assuming text means ready.
    await waitFor(() => expect(close).toBeEnabled());
    await waitFor(() => expect(close).toHaveFocus());
    const confirm = screen.getByRole("button", { name: "导入并切换" });
    confirm.focus();
    fireEvent.keyDown(confirm, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Escape", isComposing: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("does not switch a different session when an import response arrives after unmount", async () => {
    let finish!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(reply({ ok: true, module: summary }))
        .mockImplementationOnce(
          () =>
            new Promise<Response>((resolve) => {
              finish = resolve;
            }),
        ),
    );
    const { container, unmount } = render(<ModuleImporter />);
    choose(container);
    fireEvent.click(await readyImportButton());
    await waitFor(() => expect(finish).toBeDefined());
    unmount();
    await act(async () =>
      finish(
        reply({
          ok: true,
          already_installed: false,
          module: { id: "imported", title: "测试模组", version: "1.0.0" },
        }),
      ),
    );
    expect(safeSend).not.toHaveBeenCalled();
  });

  it("restores the import entry when the file picker disabled and blurred it during upload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(reply({ ok: true, module: summary })),
    );
    const { container } = render(<ModuleImporter />);
    choose(container);
    await readyImportButton();
    const close = screen.getByRole("button", { name: "关闭模组导入" });
    close.focus();
    fireEvent.keyDown(close, { key: "Escape" });
    expect(document.getElementById("btn-import-module")).toHaveFocus();
  });

  it("revokes the local import dialog when the user changes to cloud mode", async () => {
    let finish!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(reply({ ok: true, module: summary }))
        .mockImplementationOnce(
          () =>
            new Promise<Response>((resolve) => {
              finish = resolve;
            }),
        ),
    );
    const { container } = render(<ModuleImporter />);
    choose(container);
    fireEvent.click(await readyImportButton());
    await waitFor(() => expect(finish).toBeDefined());
    act(() => useAppStore.setState({ mode: "online" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () =>
      finish(
        reply({
          ok: true,
          already_installed: false,
          module: { id: "imported", title: "测试模组", version: "1.0.0" },
        }),
      ),
    );
    expect(safeSend).not.toHaveBeenCalled();
  });

  it("switches exactly once after a valid committed import", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(reply({ ok: true, module: summary }))
        .mockResolvedValueOnce(
          reply({
            ok: true,
            already_installed: false,
            module: { id: "imported", title: "测试模组", version: "1.0.0" },
          }),
        ),
    );
    const { container } = render(<ModuleImporter />);
    choose(container);
    fireEvent.click(await readyImportButton());
    await waitFor(() =>
      expect(safeSend).toHaveBeenCalledExactlyOnceWith(
        JSON.stringify({ type: "switch_module", module: "imported" }),
      ),
    );
    expect(screen.getByText(/已请求切换/)).toBeInTheDocument();
  });

  it("disconnect then reconnect does not replay a late switch; retry checks the file again", async () => {
    let finish!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(reply({ ok: true, module: summary }))
        .mockImplementationOnce(
          () =>
            new Promise<Response>((resolve) => {
              finish = resolve;
            }),
        )
        .mockResolvedValueOnce(reply({ ok: true, module: summary })),
    );
    const { container } = render(<ModuleImporter />);
    choose(container);
    fireEvent.click(await readyImportButton());
    await waitFor(() => expect(finish).toBeDefined());
    act(() => useAppStore.setState({ connection: "disconnected" }));
    expect(screen.getByText(/已安装的模组不会被撤销/)).toBeInTheDocument();
    act(() => useAppStore.setState({ connection: "connected" }));
    await act(async () =>
      finish(
        reply({
          ok: true,
          already_installed: false,
          module: { id: "imported", title: "测试模组", version: "1.0.0" },
        }),
      ),
    );
    expect(safeSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    await screen.findByText("测试模组");
    expect(screen.getByRole("button", { name: "导入并切换" })).toBeEnabled();
  });
});
