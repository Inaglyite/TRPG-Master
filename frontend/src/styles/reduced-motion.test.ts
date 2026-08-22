import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// 弹层 pop-in 属于非必要动效，必须在 prefers-reduced-motion 下禁用。
// jsdom 不应用媒体查询，这里直接断言样式表中的守卫规则与级联顺序。
const POP_IN_PANEL_IDS = [
  "model-settings-panel",
  "utility-panel",
  "save-panel",
  "modal-box",
  "module-import-panel",
];

function readStyle(path: string): string {
  return readFileSync(resolve(import.meta.dirname, path), "utf8");
}

describe("prefers-reduced-motion 弹层动效", () => {
  it("所有 pop-in 弹层都有 reduced-motion 禁用规则", () => {
    const responsive = readStyle("components/responsive.css");
    const block =
      /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/.exec(
        responsive,
      );
    expect(block, "responsive.css 缺少 reduced-motion 媒体查询").not.toBeNull();
    for (const id of POP_IN_PANEL_IDS) {
      expect(block![1]).toContain(`#${id}`);
    }
    expect(block![1]).toContain("animation: none");
  });

  it("各 pop-in 弹层确实设置了入场动画（防止本测试与实现脱节）", () => {
    const files = [
      "components/overlays.css",
      "components/save-panel.css",
      "components/controls.css",
      "components/module-import.css",
    ];
    for (const file of files) {
      expect(readStyle(file)).toContain("animation: pop-in");
    }
  });

  it("responsive.css 在 index.css 中最后导入（守卫规则才能压过 pop-in）", () => {
    const index = readStyle("index.css");
    const imports = [...index.matchAll(/@import "\.\/(.+?)";/g)].map(
      (match) => match[1],
    );
    expect(imports[imports.length - 1]).toBe("components/responsive.css");
  });

  it("统一降级块瞬时化全部 CSS 动画/过渡（覆盖 fade-in/入场/持续动效）", () => {
    const responsive = readStyle("components/responsive.css");
    const blocks = [
      ...responsive.matchAll(
        /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/g,
      ),
    ].map((match) => match[1]);
    const blanket = blocks.find(
      (block) =>
        /^\s*\*,\s*$/m.test(block) ||
        /^\s*\*\s*\{/m.test(block) ||
        block.includes("*,\n"),
    );
    expect(
      blanket,
      "缺少面向全部元素的 reduced-motion 统一降级块",
    ).toBeDefined();
    expect(blanket).toContain("animation-duration: 0.01ms !important;");
    expect(blanket).toContain("animation-iteration-count: 1 !important;");
    expect(blanket).toContain("transition-duration: 0.01ms !important;");
  });

  it("已知非必要动效样式仍存在（统一降级块确有对象可管）", () => {
    // 若未来删除这些动效，请同步精简本用例，避免统一降级块空转。
    expect(readStyle("components/online.css")).toContain(
      "animation: fade-in 0.4s",
    );
    expect(readStyle("components/start-screen.css")).toContain(
      "animation: module-content-enter",
    );
    expect(readStyle("components/messages.css")).toContain(
      "animation: dot-bounce",
    );
    expect(readStyle("components/header.css")).toContain(
      "animation: quick-save-pulse",
    );
  });
});

describe("开始新冒险换场容器布局", () => {
  it("solo-lobby-create-swap 是 flex column（CTA 的 align-self:center 才不失效）", () => {
    const online = readStyle("components/online.css");
    const block = /\.solo-lobby-create-swap \{([\s\S]*?)\}/.exec(online);
    expect(
      block,
      "online.css 缺少 .solo-lobby-create-swap 规则",
    ).not.toBeNull();
    expect(block![1]).toContain("display: flex");
    expect(block![1]).toContain("flex-direction: column");
  });
});
