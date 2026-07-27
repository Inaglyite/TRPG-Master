import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  isTrpgHealthResponse,
  packagedBackendExecutable,
  unsupportedPlatformMessage,
} from "./packaged-backend.cjs";
import { rejectionMessage } from "./reject-linux-package.cjs";

const temporaryDirectories = [];

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("packaged backend 平台与健康检查", () => {
  it("Windows 只解析真实存在的 exe", () => {
    const resources = fs.mkdtempSync(
      path.join(os.tmpdir(), "trpg-resources-"),
    );
    temporaryDirectories.push(resources);
    expect(() => packagedBackendExecutable(resources, "win32")).toThrow(
      "缺少内置后端",
    );
    const executable = path.join(resources, "backend", "trpg-server.exe");
    fs.mkdirSync(path.dirname(executable), { recursive: true });
    fs.writeFileSync(executable, "");
    expect(packagedBackendExecutable(resources, "win32")).toBe(executable);
  });

  it("Linux 不再误用 Windows 资源目录，并给出可操作提示", () => {
    expect(() => packagedBackendExecutable("/resources", "linux")).toThrow(
      "start_desktop.sh",
    );
    expect(unsupportedPlatformMessage("linux")).toContain("AppImage");
    expect(rejectionMessage()).toContain("损坏 AppImage");
    expect(rejectionMessage()).toContain("build_windows.ps1");
  });

  it("健康检查严格要求 200 和本产品 JSON，而不是任意小于 500 的响应", () => {
    const valid = JSON.stringify({
      ok: true,
      module: "mansion_of_madness",
      world_id: "local",
    });
    expect(isTrpgHealthResponse(200, valid)).toBe(true);
    expect(isTrpgHealthResponse(404, valid)).toBe(false);
    expect(isTrpgHealthResponse(200, "<html>other service</html>")).toBe(false);
    expect(isTrpgHealthResponse(200, JSON.stringify({ ok: true }))).toBe(false);
  });
});
