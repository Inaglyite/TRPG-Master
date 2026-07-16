import { useAppStore } from "./state/app-store";

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

export function applyTheme(theme: any) {
  if (!theme) return;
  if (theme.colors) {
    const map: Record<string, string> = {
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
    Object.entries(theme.colors).forEach(([key, value]) => {
      if (map[key] && isSafeThemeColor(value)) {
        document.documentElement.style.setProperty(map[key], value);
      }
    });
  }
  if (theme.fonts) {
    if (isSafeFontFamily(theme.fonts.body)) {
      document.documentElement.style.setProperty("--font", theme.fonts.body);
    }
    if (isSafeFontFamily(theme.fonts.mono)) {
      document.documentElement.style.setProperty(
        "--font-mono",
        theme.fonts.mono,
      );
    }
  }
  if (theme.title) {
    document.title = theme.title;
    useAppStore.getState().setTitle(theme.title);
  }
  if (theme.subtitle) useAppStore.getState().setSubtitle(theme.subtitle);
}
