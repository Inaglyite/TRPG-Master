/**
 * 产品身份契约回归保护（docs/ARCHITECTURE.md §9.2）：
 * 产品级名称统一为 "TRPG Game"，模组名（如"疯狂宅邸"）只允许出现在模组自身数据中。
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { DEFAULT_TITLE } from "./state/app-store";

const frontendRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const repoRoot = path.resolve(frontendRoot, "..");

function readFrontend(relative: string) {
  return readFileSync(path.join(frontendRoot, relative), "utf8");
}

function readRepo(relative: string) {
  return readFileSync(path.join(repoRoot, relative), "utf8");
}

describe("产品身份契约", () => {
  it("浏览器初始标题为 TRPG Game", () => {
    const html = readFrontend("index.html");
    expect(html).toContain("<title>TRPG Game</title>");
    expect(html).not.toContain("疯狂宅邸");
  });

  it("前端默认标题常量为 TRPG Game", () => {
    expect(DEFAULT_TITLE).toBe("TRPG Game");
  });

  it("Electron 主窗口与对话框标题为 TRPG Game", () => {
    const main = readFrontend("electron/main.cjs");
    expect(main).toContain('title: "TRPG Game"');
    expect(main).not.toContain("疯狂宅邸");
  });

  it("Windows 打包产品名与产物文件名使用 TRPG Game / trpg-game-*", () => {
    const pkg = JSON.parse(readFrontend("package.json")) as {
      description?: string;
      build?: {
        appId?: string;
        productName?: string;
        nsis?: { artifactName?: string };
        portable?: { artifactName?: string };
      };
    };
    expect(pkg.build?.productName).toBe("TRPG Game");
    expect(pkg.description ?? "").not.toContain("疯狂宅邸");
    expect(pkg.build?.nsis?.artifactName).toMatch(/^trpg-game-/);
    expect(pkg.build?.portable?.artifactName).toMatch(/^trpg-game-/);
    // appId 是安装/升级身份，本轮保持不变（契约 §2.2）
    expect(pkg.build?.appId).toBe("com.trpg.mansion-of-madness");
  });

  it("Linux 桌面启动失败通知使用产品级名称", () => {
    const script = readRepo("start_desktop.sh");
    expect(script).toContain("TRPG Game 启动失败");
    expect(script).not.toContain("疯狂宅邸启动失败");
  });

  it("模组自身名称保持原样", () => {
    const theme = JSON.parse(readRepo("mod/mansion_of_madness/theme.json")) as {
      title?: string;
    };
    expect(theme.title).toBe("疯狂宅邸");
  });
});
