import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadStructuredAsset } from "../../../api/structuredAssets";
import { ReceivedMaterials } from "./ReceivedMaterials";

vi.mock("../../../api/structuredAssets", () => ({
  loadStructuredAsset: vi.fn(),
}));
const entries = [{ id: "received-photo", label: "医生给你的照片" }];
const props = {
  entries,
  worldId: "world-a",
  scope: "owner-a:world-a:1",
  local: false,
};
const asset = {
  asset_id: "received-photo",
  label: "照片",
  asset_data_uri: "data:image/png;base64,AA==",
};
function open() {
  fireEvent.click(screen.getByRole("button", { name: /收到的图片/ }));
  const button = screen.getByRole("button", {
    name: "查看图片：医生给你的照片",
  });
  button.focus();
  fireEvent.click(button);
  return button;
}

describe("received material reading (not a grant)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });
  it("starts collapsed and fetches only on explicit viewing; closing returns focus", async () => {
    vi.mocked(loadStructuredAsset).mockResolvedValue(asset);
    render(<ReceivedMaterials {...props} />);
    expect(loadStructuredAsset).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: /查看图片/ }),
    ).not.toBeInTheDocument();
    const trigger = open();
    expect(
      await screen.findByRole("dialog", { name: entries[0].label }),
    ).toBeInTheDocument();
    expect(loadStructuredAsset).toHaveBeenCalledExactlyOnceWith(
      "world-a",
      "received-photo",
      false,
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭材料查看" }));
    expect(trigger).toHaveFocus();
  });
  it("discards a late response after an identity or recovery scope change", async () => {
    let resolve!: (value: typeof asset) => void;
    vi.mocked(loadStructuredAsset).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const view = render(<ReceivedMaterials {...props} />);
    open();
    view.rerender(
      <ReceivedMaterials {...props} scope="owner-b:world-a:2" entries={[]} />,
    );
    await act(async () => {
      resolve(asset);
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
  it("revocation immediately removes an already loaded image", async () => {
    vi.mocked(loadStructuredAsset).mockResolvedValue(asset);
    const view = render(<ReceivedMaterials {...props} />);
    open();
    await screen.findByRole("dialog");
    view.rerender(<ReceivedMaterials {...props} entries={[]} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("cancelled reads cannot reopen a late image or issue another request", async () => {
    let resolve!: (value: typeof asset) => void;
    vi.mocked(loadStructuredAsset).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    render(<ReceivedMaterials {...props} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "取消读取图片" }));
    await act(async () => {
      resolve(asset);
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(loadStructuredAsset).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /查看图片：/ })).toBeEnabled();
  });
  it("fails honestly and retries only when asked; rejects a mismatched response ID", async () => {
    vi.mocked(loadStructuredAsset)
      .mockResolvedValueOnce({ ...asset, asset_id: "other-photo" })
      .mockResolvedValueOnce(asset);
    render(<ReceivedMaterials {...props} />);
    open();
    await screen.findByRole("alert");
    expect(loadStructuredAsset).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新读取图片" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(loadStructuredAsset).toHaveBeenCalledTimes(2);
  });
});
