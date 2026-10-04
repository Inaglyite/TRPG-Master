import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../../../state/app-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { useStructuredStore } from "../../../state/structured-store";
import { sendAction } from "../../../options";
import { InvestigatorPanel } from "./InvestigatorPanel";

vi.mock("../../../options", () => ({ sendAction: vi.fn(() => true) }));
const label = "已经授权的线索图片";

function openImage() {
  const trigger = screen.getByRole("button", { name: label });
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
}

describe("clue image reading boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useStructuredStore.getState().reset();
    useOnlineStore.setState({ ...initialOnlineState });
    useInvestigatorPanelStore.setState({
      worldId: null,
      prefsByWorld: {},
      editor: null,
    });
    useAppStore.setState({
      mode: "local",
      activeWorldId: "image-world",
      connection: "connected",
      character: null,
      inputEnabled: true,
      dialog: null,
      ending: null,
      clues: {
        investigation: [
          {
            id: "authorized-clue",
            text: "作者提供的线索",
            asset: {
              file: "authorized.png",
              label,
              asset_data_uri: "data:image/png;base64,AA==",
            },
          },
        ],
      },
    });
    render(<InvestigatorPanel />);
  });

  it("opens a named reading dialog and explicit close returns focus, with no action", () => {
    const trigger = openImage();
    const viewer = screen.getByRole("dialog", { name: label });
    fireEvent.click(
      within(viewer).getByRole("button", { name: "关闭材料查看" }),
    );
    expect(
      screen.queryByRole("dialog", { name: label }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(sendAction).not.toHaveBeenCalled();
  });

  it("IME Escape does not close the image; ordinary Escape does", () => {
    openImage();
    fireEvent.keyDown(document, { key: "Escape", isComposing: true });
    expect(screen.getAllByRole("img", { name: label })).toHaveLength(2);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getAllByRole("img", { name: label })).toHaveLength(1);
    expect(sendAction).not.toHaveBeenCalled();
  });

  it("world switch removes the already-open old picture", () => {
    openImage();
    act(() =>
      useAppStore.setState({ activeWorldId: "different-world", clues: {} }),
    );
    expect(screen.queryAllByRole("img", { name: label })).toHaveLength(0);
  });

  it("same-world clue revocation removes its open picture", () => {
    openImage();
    act(() => useAppStore.setState({ clues: {} }));
    expect(screen.queryAllByRole("img", { name: label })).toHaveLength(0);
  });

  it("failed images offer a bounded recovery path without submitting a game action", () => {
    openImage();
    fireEvent.error(screen.getAllByRole("img", { name: label }).at(-1)!);
    expect(screen.getByRole("alert")).toHaveTextContent("图片未能加载");
    fireEvent.click(screen.getByRole("button", { name: "重新加载图片" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在加载图片");
    expect(sendAction).not.toHaveBeenCalled();
  });
});
