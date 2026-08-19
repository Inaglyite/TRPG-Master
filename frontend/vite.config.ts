import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// 构建 ID 用于判断"版本更新后第一次进入"：优先取部署注入的 TRPG_BUILD_ID，
// 本地构建退化为 git short sha，再退化为时间戳（纯 dev 场景）。
function resolveBuildId(): string {
  if (process.env.TRPG_BUILD_ID) return process.env.TRPG_BUILD_ID;
  try {
    return execSync("git rev-parse --short HEAD", { encoding: "utf-8" }).trim();
  } catch {
    return `dev-${Date.now()}`;
  }
}

const BOOT_ASSET_EXT = /\.(png|jpe?g|webp|avif|gif|woff2?)$/i;

function collectBootAssets(dir: string, prefix: string, out: string[]): void {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = `${prefix}${entry.name}`;
    if (entry.isDirectory()) {
      collectBootAssets(path.join(dir, entry.name), `${rel}/`, out);
    } else if (BOOT_ASSET_EXT.test(entry.name)) {
      out.push(rel);
    }
  }
}

// 构建结束后把 dist/assets 下所有图片/字体列进 boot-manifest.json，
// 启动加载屏据此在首屏渲染前一次性预载全部 UI 资源。
function bootManifestPlugin(): Plugin {
  return {
    name: "trpg-boot-manifest",
    apply: "build",
    closeBundle() {
      const outDir = path.resolve(__dirname, "dist");
      const assetsDir = path.join(outDir, "assets");
      if (!fs.existsSync(assetsDir)) return;
      const files: string[] = [];
      collectBootAssets(assetsDir, "assets/", files);
      files.sort();
      fs.writeFileSync(
        path.join(outDir, "boot-manifest.json"),
        `${JSON.stringify({ files })}\n`,
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), bootManifestPlugin()],
  root: ".",
  // 用相对路径，这样 Electron 用 file:// 加载 dist/index.html 时
  // 资源能正确解析为 ./assets/... 而不是 /assets/...（后者会指向文件系统根）
  base: "./",
  define: {
    __APP_BUILD_ID__: JSON.stringify(resolveBuildId()),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    proxy: {
      "/ws": {
        target: "ws://localhost:8765",
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.test.{ts,tsx}", "electron/*.test.mjs"],
  },
});
