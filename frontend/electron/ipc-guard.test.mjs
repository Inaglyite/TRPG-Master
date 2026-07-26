import { describe, expect, it } from "vitest";

import {
  isApprovedCloudSenderUrl,
  isNavigationAllowed,
  isTrustedSenderUrl,
} from "./ipc-guard.cjs";

const TRUSTED = "file:///app/dist/index.html";
const packaged = {
  isDev: false,
  devServerUrl: "http://127.0.0.1:5173",
  trustedFileUrl: TRUSTED,
  approvedCloudOrigin: null,
};
const dev = { ...packaged, isDev: true };

describe("isTrustedSenderUrl（打包模式）", () => {
  it("只允许确切的内置 dist/index.html URL", () => {
    expect(isTrustedSenderUrl(TRUSTED, packaged)).toBe(true);
  });

  it("拒绝其他 file:// 与所有远程页面", () => {
    expect(isTrustedSenderUrl("file:///etc/passwd", packaged)).toBe(false);
    expect(isTrustedSenderUrl("file:///app/dist/other.html", packaged)).toBe(false);
    expect(isTrustedSenderUrl("file:///app/dist/index.html.evil", packaged)).toBe(false);
    expect(isTrustedSenderUrl("https://trpg.example.com/", packaged)).toBe(false);
    expect(isTrustedSenderUrl("data:text/html,<html></html>", packaged)).toBe(false);
  });

  it("拒绝非法输入", () => {
    expect(isTrustedSenderUrl("", packaged)).toBe(false);
    expect(isTrustedSenderUrl("not a url", packaged)).toBe(false);
    expect(isTrustedSenderUrl(null, packaged)).toBe(false);
    expect(isTrustedSenderUrl(undefined, packaged)).toBe(false);
  });
});

describe("isTrustedSenderUrl（开发模式）", () => {
  it("只允许 dev server origin", () => {
    expect(isTrustedSenderUrl("http://127.0.0.1:5173/", dev)).toBe(true);
    expect(isTrustedSenderUrl("http://127.0.0.1:5173/src/main.tsx", dev)).toBe(true);
    expect(isTrustedSenderUrl(TRUSTED, dev)).toBe(false);
    expect(isTrustedSenderUrl("http://localhost:5173/", dev)).toBe(false);
  });
});

describe("isNavigationAllowed", () => {
  it("打包模式放行内置页面与 about:blank", () => {
    expect(isNavigationAllowed(TRUSTED, packaged)).toBe(true);
    expect(isNavigationAllowed("about:blank", packaged)).toBe(true);
  });

  it("未批准时拒绝云端 origin，批准后放行其下页面", () => {
    expect(isNavigationAllowed("https://trpg.example.com/?mode=online", packaged)).toBe(false);
    const approved = { ...packaged, approvedCloudOrigin: "https://trpg.example.com" };
    expect(isNavigationAllowed("https://trpg.example.com/?mode=online", approved)).toBe(true);
    expect(isNavigationAllowed("https://trpg.example.com/ws/room", approved)).toBe(true);
    expect(isNavigationAllowed("https://evil.example.com/", approved)).toBe(false);
  });

  it("拒绝其他 file:// 与非法 URL", () => {
    expect(isNavigationAllowed("file:///etc/passwd", packaged)).toBe(false);
    expect(isNavigationAllowed("file:///app/dist/other.html", packaged)).toBe(false);
    expect(isNavigationAllowed("not a url", packaged)).toBe(false);
  });

  it("开发模式只允许 dev server origin", () => {
    expect(isNavigationAllowed("http://127.0.0.1:5173/", dev)).toBe(true);
    expect(isNavigationAllowed("file:///app/dist/index.html", dev)).toBe(false);
  });
});

describe("isApprovedCloudSenderUrl", () => {
  it("只接受当前批准云端 origin 下的页面", () => {
    expect(
      isApprovedCloudSenderUrl(
        "https://game.example/room?id=1",
        "https://game.example",
      ),
    ).toBe(true);
    expect(
      isApprovedCloudSenderUrl(
        "https://evil.example/",
        "https://game.example",
      ),
    ).toBe(false);
    expect(isApprovedCloudSenderUrl("not a url", "https://game.example")).toBe(
      false,
    );
  });
});
