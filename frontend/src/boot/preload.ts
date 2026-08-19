/** 启动预载：版本更新后的首次进入把全部 UI 资源一次性拉入 HTTP 缓存。 */

import { useAppStore } from "../state/app-store";

const BOOT_BUILD_KEY = "trpg-boot-build";
const DEFAULT_CONCURRENCY = 6;
const DEFAULT_TIMEOUT_MS = 20_000;
const BG_WAIT_TIMEOUT_MS = 3_000;
const CONNECTION_WAIT_TIMEOUT_MS = 5_000;
const BG_IMAGE_VAR = "--module-bg-image";

/** 读取构建期生成的资源清单；dev / Electron file:// 下不可用时返回空。 */
export async function loadBootManifest(): Promise<string[]> {
  try {
    const response = await fetch("boot-manifest.json", { cache: "no-store" });
    if (!response.ok) return [];
    const data: unknown = await response.json();
    if (!data || typeof data !== "object") return [];
    const files = (data as { files?: unknown }).files;
    if (!Array.isArray(files)) return [];
    return files.filter((file): file is string => typeof file === "string");
  } catch {
    // Electron file:// 下相对路径 fetch 不可用；本地资源随读随有，无需预载。
    return [];
  }
}

/** 本次构建是否首次启动（含浏览器第一次访问）。 */
export function isBuildChanged(
  storage: Pick<Storage, "getItem"> = window.localStorage,
): boolean {
  try {
    return storage.getItem(BOOT_BUILD_KEY) !== __APP_BUILD_ID__;
  } catch {
    return true;
  }
}

export function recordBuildBooted(
  storage: Pick<Storage, "setItem"> = window.localStorage,
): void {
  try {
    storage.setItem(BOOT_BUILD_KEY, __APP_BUILD_ID__);
  } catch {
    // 隐私模式等写失败场景：下次进入仍走完整预载，无副作用。
  }
}

function preloadOne(url: string): Promise<void> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve();
    image.onerror = () => resolve(); // 单个资源失败不阻塞进入
    image.src = url;
  });
}

/** 并发受限地预载图片；整体超时后强制完成，失败资源计入进度。 */
export async function preloadImages(
  urls: string[],
  onProgress?: (loaded: number, total: number) => void,
  options: { concurrency?: number; timeoutMs?: number } = {},
): Promise<void> {
  const total = urls.length;
  if (!total) return;
  const concurrency = Math.max(1, options.concurrency ?? DEFAULT_CONCURRENCY);
  let loaded = 0;
  let cursor = 0;
  const worker = async () => {
    while (cursor < urls.length) {
      const url = urls[cursor++];
      await preloadOne(url);
      loaded += 1;
      onProgress?.(loaded, total);
    }
  };
  const workers = Array.from({ length: Math.min(concurrency, total) }, () =>
    worker(),
  );
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  });
  try {
    await Promise.race([Promise.all(workers), timeout]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function readModuleBgUrl(): string | null {
  const value = document.documentElement.style.getPropertyValue(BG_IMAGE_VAR);
  const match = /url\("([^"]+)"\)/.exec(value);
  return match ? match[1] : null;
}

/** local 模式：等 WS 主题消息写入模组背景图 URL；无背景或超时返回 null。 */
export function waitForModuleBgUrl(
  timeoutMs: number = BG_WAIT_TIMEOUT_MS,
): Promise<string | null> {
  const existing = readModuleBgUrl();
  if (existing) return Promise.resolve(existing);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      observer.disconnect();
      resolve(null);
    }, timeoutMs);
    const observer = new MutationObserver(() => {
      const url = readModuleBgUrl();
      if (url) {
        clearTimeout(timer);
        observer.disconnect();
        resolve(url);
      }
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["style"],
    });
  });
}

/** local 模式：等 WS 首连完成（首批存档/角色数据随连接推送）；超时放行。 */
export function waitForConnection(
  timeoutMs: number = CONNECTION_WAIT_TIMEOUT_MS,
): Promise<void> {
  if (useAppStore.getState().connection === "connected") {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      unsubscribe();
      resolve();
    }, timeoutMs);
    const unsubscribe = useAppStore.subscribe((state) => {
      if (state.connection === "connected") {
        clearTimeout(timer);
        unsubscribe();
        resolve();
      }
    });
  });
}
