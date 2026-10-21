import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AvatarDisc } from "./AvatarDisc";

describe("AvatarDisc", () => {
  it("falls back from a broken data URI to the HTTP URL, then to the initial badge", () => {
    const avatar = {
      asset_data_uri: "data:image/png;base64,bm90LWltYWdl",
      asset_url: "/api/assets/mod/npc.png",
      alt: "金三爷肖像",
    };
    const { container, rerender } = render(
      <AvatarDisc name="金三爷" avatar={avatar} family="npc" />,
    );
    let img = container.querySelector("img")!;
    expect(img.src).toContain("data:image/png");
    // 内联图失败：尝试 URL，不直接掉到徽章
    fireEvent.error(img);
    img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/api/assets/mod/npc.png");
    // URL 也失败：退回姓名首字徽章
    fireEvent.error(img);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".avatar-fallback")!.textContent).toBe("金");
    // 徽章状态稳定，不会因为重渲染又回到坏图
    rerender(<AvatarDisc name="金三爷" avatar={avatar} family="npc" />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".avatar-fallback")!.textContent).toBe("金");
  });

  it("resets the failure state when the speaker identity changes", () => {
    const { container, rerender } = render(
      <AvatarDisc
        name="金三爷"
        avatar={{ asset_url: "/broken.png" }}
        family="npc"
      />,
    );
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector(".avatar-fallback")).not.toBeNull();
    rerender(
      <AvatarDisc
        name="苏晚晴"
        avatar={{ asset_url: "/ok.png" }}
        family="npc"
      />,
    );
    expect(screen.getByRole("img").getAttribute("src")).toBe("/ok.png");
  });

  it("keeps the initial badge for speakers without any avatar", () => {
    const { container } = render(
      <AvatarDisc name="伙计" avatar={null} family="npc" />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".avatar-fallback")!.textContent).toBe("伙");
  });
});
