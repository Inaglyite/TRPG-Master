import { beforeEach, describe, expect, it, vi } from "vitest";

import { applyTheme } from "./theme";
import {
  DEFAULT_SUBTITLE,
  DEFAULT_TITLE,
  useAppStore,
} from "./state/app-store";

// jsdom 未实现 CSS.supports，用最小启发式桩代替：
// 仅接受 hex / rgb()/hsl() / 单词颜色名。
vi.stubGlobal("CSS", {
  supports: (_property: string, value: string) => {
    const v = value.trim();
    return (
      /^#[0-9a-f]{3,8}$/i.test(v) ||
      /^(rgb|hsl)a?\(/i.test(v) ||
      /^[a-z]+$/i.test(v)
    );
  },
});

const MANAGED_PROBE_VARS = [
  "--bg",
  "--crimson",
  "--font",
  "--font-display",
  "--module-bg-image",
];

function clearManagedVars() {
  const root = document.documentElement;
  MANAGED_PROBE_VARS.forEach((name) => root.style.removeProperty(name));
  root.removeAttribute("data-fx-grain");
  root.removeAttribute("data-fx-flicker");
  root.removeAttribute("data-fx-vignette");
}

describe("applyTheme", () => {
  beforeEach(() => {
    clearManagedVars();
    useAppStore.setState({
      activeWorldId: null,
      activeModule: null,
      title: DEFAULT_TITLE,
      subtitle: DEFAULT_SUBTITLE,
      description: "",
      startButtonText: "",
    });
  });

  it("maps known color keys to CSS variables", () => {
    applyTheme({ colors: { bg: "#0a0608", crimson: "#6b1218" } });
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--bg")).toBe("#0a0608");
    expect(style.getPropertyValue("--crimson")).toBe("#6b1218");
  });

  it("clears variables from the previous module before applying", () => {
    applyTheme({ colors: { crimson: "#6b1218", bg: "#0a0608" } });
    applyTheme({ colors: { bg: "#14100c" } });
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--bg")).toBe("#14100c");
    expect(style.getPropertyValue("--crimson")).toBe("");
  });

  it("ignores unknown keys and unsafe color values", () => {
    applyTheme({
      colors: { bg: "not-a-color", evil: "#fff", gold: "#c8a24e" },
    });
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--bg")).toBe("");
    expect(style.getPropertyValue("--gold")).toBe("#c8a24e");
  });

  it("maps fonts.heading to --font-display and rejects unsafe fonts", () => {
    applyTheme({
      fonts: {
        heading: '"Palatino Linotype", serif',
        body: "serif; color: red",
        mono: "url(http://evil)",
      },
    });
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--font-display")).toBe(
      '"Palatino Linotype", serif',
    );
    expect(style.getPropertyValue("--font")).toBe("");
    expect(style.getPropertyValue("--font-mono")).toBe("");
  });

  it("stores title/subtitle/description/startButtonText with defaults", () => {
    applyTheme({
      title: "猩红文档",
      subtitle: "Crimson Letters",
      description: "密斯卡托尼克大学学者离奇死亡……",
      startButtonText: "打开猩红文档",
    });
    const state = useAppStore.getState();
    expect(state.title).toBe("猩红文档");
    expect(state.subtitle).toBe("Crimson Letters");
    expect(state.description).toBe("密斯卡托尼克大学学者离奇死亡……");
    expect(state.startButtonText).toBe("打开猩红文档");
    expect(document.title).toBe("猩红文档");
  });

  it("resets text fields to defaults when the theme omits them", () => {
    applyTheme({ title: "猩红文档", description: "d", startButtonText: "s" });
    applyTheme({ colors: {} });
    const state = useAppStore.getState();
    expect(state.title).toBe(DEFAULT_TITLE);
    expect(state.subtitle).toBe(DEFAULT_SUBTITLE);
    expect(state.description).toBe("");
    expect(state.startButtonText).toBe("");
  });

  it("sets data-fx-* attributes for disabled effects and clears them later", () => {
    applyTheme({ effects: { grain: false, flicker: false } });
    const root = document.documentElement;
    expect(root.getAttribute("data-fx-grain")).toBe("off");
    expect(root.getAttribute("data-fx-flicker")).toBe("off");
    expect(root.getAttribute("data-fx-vignette")).toBeNull();

    applyTheme({ effects: { grain: true } });
    expect(root.getAttribute("data-fx-grain")).toBeNull();
    expect(root.getAttribute("data-fx-flicker")).toBeNull();
  });

  it("builds a module background image URL from the active module", () => {
    useAppStore.getState().setWorld("world-1", "猩红文档");
    applyTheme({ backgroundImage: "莱特的小屋.png" });
    const value =
      document.documentElement.style.getPropertyValue("--module-bg-image");
    expect(value).toBe(
      'url("http://localhost:8765/api/assets/%E7%8C%A9%E7%BA%A2%E6%96%87%E6%A1%A3/%E8%8E%B1%E7%89%B9%E7%9A%84%E5%B0%8F%E5%B1%8B.png")',
    );
  });

  it("rejects unsafe backgroundImage values and missing modules", () => {
    const style = document.documentElement.style;
    applyTheme({ backgroundImage: "../secret.png" });
    expect(style.getPropertyValue("--module-bg-image")).toBe("");
    applyTheme({ backgroundImage: "https://evil.example/bg.png" });
    expect(style.getPropertyValue("--module-bg-image")).toBe("");
    applyTheme({ backgroundImage: "no-extension" });
    expect(style.getPropertyValue("--module-bg-image")).toBe("");
    // 合法路径但没有活动模组时同样不注入
    applyTheme({ backgroundImage: "bg.png" });
    expect(style.getPropertyValue("--module-bg-image")).toBe("");
  });
});
