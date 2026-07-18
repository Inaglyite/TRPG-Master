import { resetDice3DTheme } from "./dice3d/controller";
import {
  DEFAULT_SUBTITLE,
  DEFAULT_TITLE,
  useAppStore,
} from "./state/app-store";

function isSafeThemeColor(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 80 &&
    CSS.supports("color", value)
  );
}

function isSafeFontFamily(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 240 &&
    !/[;{}<>]/.test(value) &&
    !/url\s*\(/i.test(value)
  );
}

function isSafeText(value: unknown, maxLength: number): value is string {
  return typeof value === "string" && value.length <= maxLength;
}

const COLOR_VAR_MAP: Record<string, string> = {
  bg: "--bg",
  bg2: "--bg2",
  bg3: "--bg3",
  bg4: "--bg4",
  text: "--text",
  textDim: "--text-dim",
  textFaint: "--text-faint",
  gold: "--gold",
  goldDim: "--gold-dim",
  goldBright: "--gold-bright",
  red: "--red",
  redDim: "--red-dim",
  redBright: "--red-bright",
  crimson: "--crimson",
  crimsonBright: "--crimson-bright",
  green: "--green",
  blue: "--blue",
  blueBright: "--blue-bright",
  purple: "--purple",
  ink: "--ink",
  border: "--border",
  borderBright: "--border-bright",
};

const FONT_VAR_MAP: Record<string, string> = {
  body: "--font",
  mono: "--font-mono",
  heading: "--font-display",
};

const BG_IMAGE_VAR = "--module-bg-image";

// applyTheme 注入的全部 CSS 变量。应用新主题前统一清除，
// 保证切换模组时未提供的字段回退到 tokens.css 默认值。
const MANAGED_VARS = [
  ...Object.values(COLOR_VAR_MAP),
  ...Object.values(FONT_VAR_MAP),
  BG_IMAGE_VAR,
];

// 主题可通过 effects 关闭的氛围特效，对应 <html data-fx-*="off">。
const FX_FLAGS = ["grain", "flicker", "vignette"] as const;

const BG_IMAGE_EXT = /\.(png|jpe?g|webp|gif|avif)$/i;

// backgroundImage 必须是模组 assets/ 内的相对路径，防止任意外链或路径穿越。
function isSafeAssetPath(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 200 &&
    !value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.includes("..") &&
    !value.includes("\\") &&
    !/^[a-z][a-z0-9+.-]*:/i.test(value) &&
    BG_IMAGE_EXT.test(value)
  );
}

function moduleAssetUrl(path: string): string | null {
  const moduleName = useAppStore.getState().activeModule;
  if (!moduleName) return null;
  const host = location.hostname || "127.0.0.1";
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  return `http://${host}:8765/api/assets/${encodeURIComponent(moduleName)}/${encodedPath}`;
}

export function applyTheme(theme: any) {
  if (!theme || typeof theme !== "object") return;
  const root = document.documentElement;

  // 先清掉上一个模组注入的全部受管变量与特效开关，再应用新主题。
  MANAGED_VARS.forEach((name) => root.style.removeProperty(name));
  FX_FLAGS.forEach((flag) => root.removeAttribute(`data-fx-${flag}`));
  resetDice3DTheme();

  const colors = theme.colors;
  if (colors && typeof colors === "object") {
    Object.entries(colors).forEach(([key, value]) => {
      const cssVar = COLOR_VAR_MAP[key];
      if (cssVar && isSafeThemeColor(value)) {
        root.style.setProperty(cssVar, value);
      }
    });
  }

  const fonts = theme.fonts;
  if (fonts && typeof fonts === "object") {
    Object.entries(FONT_VAR_MAP).forEach(([key, cssVar]) => {
      const value = (fonts as Record<string, unknown>)[key];
      if (isSafeFontFamily(value)) {
        root.style.setProperty(cssVar, value);
      }
    });
  }

  const store = useAppStore.getState();
  const title = isSafeText(theme.title, 120) ? theme.title : DEFAULT_TITLE;
  document.title = title;
  store.setTitle(title);
  store.setSubtitle(
    isSafeText(theme.subtitle, 200) ? theme.subtitle : DEFAULT_SUBTITLE,
  );
  store.setDescription(
    isSafeText(theme.description, 600) ? theme.description : "",
  );
  store.setStartButtonText(
    isSafeText(theme.startButtonText, 40) ? theme.startButtonText : "",
  );

  if (isSafeAssetPath(theme.backgroundImage)) {
    const url = moduleAssetUrl(theme.backgroundImage);
    if (url) root.style.setProperty(BG_IMAGE_VAR, `url("${url}")`);
  }

  const effects = theme.effects;
  if (effects && typeof effects === "object") {
    FX_FLAGS.forEach((flag) => {
      if ((effects as Record<string, unknown>)[flag] === false) {
        root.setAttribute(`data-fx-${flag}`, "off");
      }
    });
  }
}
