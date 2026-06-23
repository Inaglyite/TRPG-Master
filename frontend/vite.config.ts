import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  // 用相对路径，这样 Electron 用 file:// 加载 dist/index.html 时
  // 资源能正确解析为 ./assets/... 而不是 /assets/...（后者会指向文件系统根）
  base: "./",
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
});
