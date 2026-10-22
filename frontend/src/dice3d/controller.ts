import type { VisualDie } from "../state/message-store";

/**
 * 3D 骰控制器：懒加载 @3d-dice/dice-box-threejs（three+cannon-es 经动态
 * import 进入独立 chunk，不进主包），单例 DiceBox 附着在聊天面板的
 * 覆盖层上。所有路径都有权威文字结果回退：
 * reduced-motion、无 WebGL、初始化失败/超时、物理失败/超时、非标准面数、
 * 忙线并发。动画只是表现，骰面永远来自服务端。
 */

type DiceBoxLike = {
  initialize(): Promise<void>;
  roll(notation: string): Promise<unknown>;
  clearDice(): void;
  renderer?: {
    dispose?: () => void;
    forceContextLoss?: () => void;
    domElement?: HTMLCanvasElement;
  };
};

// dice-box 物理模型支持的面数（d10 数字骰单独处理）
const SUPPORTED_SIDES = new Set([2, 4, 6, 8, 10, 12, 20]);
// 初始化预算覆盖动态 import（chunk 加载）与 DiceBox.initialize()：
// 正常 <1s，Electron 首载/慢盘放宽到 8s；超时视为初始化失败，永久回退文字。
export const INIT_TIMEOUT_MS = 8000;
// 物理投掷预算：正常定格 1–3s，5s 兜底。
export const ROLL_TIMEOUT_MS = 5000;

let box: DiceBoxLike | null = null;
let boxInit: Promise<DiceBoxLike | null> | null = null;
let boxFailed = false;
let overlayEl: HTMLDivElement | null = null;
let rolling = false;
let rollSeq = 0;
let skipCurrent: (() => void) | null = null;
let boxGeneration = 0;
let abortBoxInit: (() => void) | null = null;
// Chunk loading is shared; cancelled generations only abandon their instance.
let diceModule: Promise<typeof import("@3d-dice/dice-box-threejs")> | null =
  null;

/** 诊断日志：init/roll 的开始、结束、超时、跳过、取消，含耗时与消息 ID。 */
function logDice3D(event: string, detail: Record<string, unknown> = {}): void {
  console.debug(`[dice3d] ${event}`, detail);
}

/**
 * GPU/版本诊断快照：供真实 Electron 环境排查卡死时对照控制器状态。
 * 自身绝不抛错。
 */
export function dice3DDiagnostics(): Record<string, unknown> {
  const gpu: Record<string, unknown> = {};
  try {
    const canvas = document.createElement("canvas");
    const gl = (canvas.getContext("webgl2") ||
      canvas.getContext("webgl")) as WebGLRenderingContext | null;
    if (gl) {
      const info = gl.getExtension("WEBGL_debug_renderer_info");
      gpu.version = gl.getParameter(gl.VERSION);
      gpu.renderer = info
        ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL)
        : gl.getParameter(gl.RENDERER);
    }
  } catch {
    // 诊断钩子自身不得抛错
  }
  return { boxFailed, rolling, rollSeq, boxGeneration, ...gpu };
}

function hasWebGL(): boolean {
  // jsdom 暴露 canvas.getContext，但调用只会向 stderr 输出 not-implemented。
  if (typeof WebGLRenderingContext === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

/** 该组骰子能否走 3D（不满足时调用方应直接走 CSS 回退）。 */
export function isDice3DEligible(dice: VisualDie[]): boolean {
  if (!dice.length || boxFailed) return false;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches)
    return false;
  if (!hasWebGL()) return false;
  return toNotation(dice) !== null;
}

/** VisualDie[] → dice-box 投掷记法（如 "2d10@3,2"），不支持时返回 null。 */
function toNotation(dice: VisualDie[]): string | null {
  const types: string[] = [];
  const results: number[] = [];
  for (const die of dice) {
    if (die.min === 0 && die.max === 9) {
      // d10 数字骰（十位/个位），面值 0–9，@0 会停在标 "0" 的面
      types.push("d10");
      results.push(die.final);
    } else if (die.min === 1 && die.max === 100) {
      // 平面 d100 → 拆成十位/个位两颗 d10
      const final = Math.max(1, Math.min(100, Math.round(die.final)));
      types.push("d10", "d10");
      results.push(final === 100 ? 0 : Math.floor(final / 10));
      results.push(final === 100 ? 0 : final % 10);
    } else if (SUPPORTED_SIDES.has(die.max)) {
      types.push(`d${die.max}`);
      results.push(die.final);
    } else {
      return null;
    }
  }
  // 混型投掷记法的结果顺序不可靠，混型时整体回退 CSS
  if (new Set(types).size !== 1) return null;
  return `${types.length}${types[0]}@${results.join(",")}`;
}

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

function buildColorset(): Record<string, unknown> {
  return {
    name: "keeper-theme",
    foreground: cssVar("--gold-bright", "#ecd07a"),
    background: cssVar("--bg3", "#2a221a"),
    outline: cssVar("--gold-dim", "#8a6e30"),
    texture: "none",
    material: "plastic",
  };
}

function disposeBox(instance: DiceBoxLike | null): void {
  if (!instance) return;
  for (const cleanup of [
    () => instance.clearDice(),
    () => instance.renderer?.dispose?.(),
    () => instance.renderer?.forceContextLoss?.(),
    () => instance.renderer?.domElement?.remove(),
  ]) {
    try {
      cleanup();
    } catch (error) {
      console.warn("[dice3d] 释放 3D 骰资源失败", error);
    }
  }
}

function ensureOverlay(): HTMLDivElement {
  // 宿主面板被整体重渲染/重建后，旧节点已脱离文档，需要重新挂载。
  if (overlayEl?.isConnected) return overlayEl;
  const el = document.createElement("div");
  el.className = "dice-3d-overlay hidden";
  el.innerHTML =
    '<div id="dice-3d-scene" class="dice-3d-scene"></div>' +
    '<div class="dice-3d-skip">点击跳过</div>';
  el.addEventListener("click", () => {
    skipCurrent?.();
  });
  (document.getElementById("chat-panel") || document.body).appendChild(el);
  overlayEl = el;
  return el;
}

/**
 * 初始化单例 DiceBox。动态 import 与 initialize() 共享 INIT_TIMEOUT_MS
 * 预算：任一侧永不完成时，超时分支让本 Promise 在预算内落地；
 * 超时/超代之后才 resolve 的实例被立即释放，不会复活已降级的投掷。
 */
function ensureBox(): Promise<DiceBoxLike | null> {
  if (box) return Promise.resolve(box);
  if (boxFailed) return Promise.resolve(null);
  if (!boxInit) {
    const generation = boxGeneration;
    const startedAt = Date.now();
    logDice3D("init start", { generation });
    let expired = false;
    let abandoned = false;
    let pendingInstance: DiceBoxLike | null = null;
    let abort!: () => void;
    let timer = 0;
    const cancelled = new Promise<null>((resolve) => {
      abort = () => {
        abandoned = true;
        disposeBox(pendingInstance);
        resolve(null);
      };
    });
    abortBoxInit = abort;
    const attempt = (async (): Promise<DiceBoxLike | null> => {
      try {
        const { default: DiceBox } = await (diceModule ??=
          import("@3d-dice/dice-box-threejs"));
        // 等待方已跳过/取消时不再构建实例、不显示遮罩：隐藏构建会让
        // DiceBox 读到 0 尺寸，场景渲染为空，作废本次初始化留给下次重建。
        if (expired || abandoned || generation !== boxGeneration || !rolling) {
          abandoned = true;
          return null;
        }
        ensureOverlay();
        // 构造前必须让覆盖层可见：DiceBox 在构造时读取容器
        // clientWidth/clientHeight，display:none 下为 0，场景会渲染为空。
        showOverlay();
        const instance = new DiceBox("#dice-3d-scene", {
          sounds: false,
          shadows: true,
          theme_customColorset: buildColorset(),
          theme_material: "plastic",
          light_intensity: 0.9,
          strength: 1,
        });
        pendingInstance = instance as DiceBoxLike;
        await instance.initialize();
        if (expired || abandoned || generation !== boxGeneration) {
          disposeBox(pendingInstance);
          return null;
        }
        return instance as DiceBoxLike;
      } catch (error) {
        disposeBox(pendingInstance);
        if (!expired && generation === boxGeneration) {
          console.warn(
            "[dice3d] 初始化失败，回退文字结果",
            error,
            dice3DDiagnostics(),
          );
        }
        return null;
      }
    })();
    boxInit = Promise.race([
      attempt,
      cancelled,
      new Promise<null>((resolve) => {
        timer = window.setTimeout(() => {
          expired = true;
          disposeBox(pendingInstance);
          console.warn(
            `[dice3d] 初始化超过 ${INIT_TIMEOUT_MS}ms 未完成，回退文字结果`,
            dice3DDiagnostics(),
          );
          resolve(null);
        }, INIT_TIMEOUT_MS);
      }),
    ]).then((instance) => {
      window.clearTimeout(timer);
      // Older attempts cannot clear the new attempt's promise or overlay.
      if (abortBoxInit === abort) abortBoxInit = null;
      if (instance && !expired && !abandoned && generation === boxGeneration) {
        box = instance;
        instance.renderer?.domElement?.addEventListener(
          "webglcontextlost",
          () => {
            if (box !== instance) return;
            box = null;
            boxFailed = true;
            boxInit = null;
            cancelDice3D();
            disposeBox(instance);
            logDice3D("context lost", { generation });
          },
          { once: true },
        );
        logDice3D("init ok", { ms: Date.now() - startedAt });
        return box;
      }
      // 超时后迟到、主题已重建或初始化失败：释放实例并收起覆盖层。
      if (instance) disposeBox(instance);
      if (abandoned || generation !== boxGeneration) {
        // 等待方已离开：初始化作废但不视为失败，下次投掷重新初始化
        return null;
      }
      boxFailed = true;
      hideOverlay();
      logDice3D("init failed", { expired, ms: Date.now() - startedAt });
      return null;
    });
  }
  return boxInit;
}

/** 模组换色后重建骰子（下次投掷时以新主题色初始化）。 */
export function resetDice3DTheme(): void {
  cancelDice3D();
  boxGeneration += 1;
  disposeBox(box);
  box = null;
  boxInit = null;
  boxFailed = false;
  const scene = overlayEl?.querySelector("#dice-3d-scene");
  if (scene) scene.innerHTML = "";
}

function showOverlay(): void {
  ensureOverlay().classList.remove("hidden");
}

function hideOverlay(): void {
  overlayEl?.classList.add("hidden");
}

/** 当前有进行中的 3D 投掷时，新消息应走 CSS 回退。 */
export function isDice3DBusy(): boolean {
  return rolling;
}

/**
 * 投掷一组 3D 骰。覆盖层与跳过回调从初始化 pending 起就安装，
 * 因此任何阶段（import 卡住、initialize 卡住、物理卡住）都能在预算内
 * 或被用户立即终结。resolve 时机：物理定格、超时兜底或用户点击跳过；
 * reject 时机：3D 不可用（记法不支持/忙线/初始化失败）或被取消——
 * 调用方的 finally 落定服务端权威文字结果。
 */
export async function rollDice3D(
  dice: VisualDie[],
  messageId?: string,
): Promise<void> {
  const notation = toNotation(dice);
  if (!notation || rolling) throw new Error("dice3d unavailable");
  // 在第一个 await 之前占位，避免两条消息同时穿过 busy 检查。
  rolling = true;
  const seq = ++rollSeq;
  const startedAt = Date.now();
  logDice3D("roll start", { messageId, notation, seq });
  showOverlay();
  let skipRequested = false;
  let abortInit: (() => void) | null = null;
  // 本投掷最近一次安装的跳过回调；finally 只在回调仍属于本投掷时摘除，
  // 避免误清接管后的下一次投掷。
  let skipFn: (() => void) | null = null;
  const installSkip = (fn: (() => void) | null) => {
    skipFn = fn;
    skipCurrent = fn;
  };
  installSkip(() => {
    skipRequested = true;
    logDice3D("skip", { messageId, seq, phase: "init" });
    abandonInitialization();
    abortInit?.();
  });
  try {
    const instance = await Promise.race([
      ensureBox(),
      new Promise<null>((resolve) => {
        abortInit = () => resolve(null);
      }),
    ]);
    if (!instance || seq !== rollSeq) {
      const cancelled = skipRequested || seq !== rollSeq;
      logDice3D("roll end", {
        messageId,
        seq,
        reason: cancelled ? "cancelled" : "init-failed",
        ms: Date.now() - startedAt,
      });
      throw new Error(cancelled ? "dice3d cancelled" : "dice3d init failed");
    }
    const activeInstance = instance;
    await new Promise<void>((resolve) => {
      let finished = false;
      const timer = window.setTimeout(
        () => finish(true, "timeout"),
        ROLL_TIMEOUT_MS,
      );
      installSkip(() => {
        skipRequested = true;
        finish(true, "skip");
      });
      function finish(clear = false, reason = "settled") {
        if (finished) return;
        finished = true;
        window.clearTimeout(timer);
        // 只有当前投掷才能清物理场景，避免误清接管后的下一次投掷；
        // 清理自身抛错不得阻断 Promise 落地。
        if (clear && seq === rollSeq) {
          try {
            activeInstance.clearDice();
          } catch (error) {
            console.warn("[dice3d] 清理骰子失败", error);
          }
        }
        if (skipCurrent === skipFn) skipCurrent = null;
        logDice3D("roll end", {
          messageId,
          seq,
          reason,
          ms: Date.now() - startedAt,
        });
        resolve();
      }
      Promise.resolve()
        .then(() => activeInstance.roll(notation))
        .then(
          () => finish(),
          (error) => {
            console.warn("[dice3d] 物理投掷失败，回退文字结果", error);
            finish(true, "roll-error");
          },
        );
    });
  } finally {
    if (skipCurrent === skipFn) skipCurrent = null;
    if (seq === rollSeq) {
      rolling = false;
      hideOverlay();
    }
  }
}

/** 组件卸载时调用：立即收起覆盖层并让等待中的 Promise 落地。 */
export function cancelDice3D(): void {
  logDice3D("cancel", { seq: rollSeq });
  rollSeq += 1;
  rolling = false;
  abandonInitialization();
  if (box) {
    try {
      box.clearDice();
    } catch (error) {
      console.warn("[dice3d] 清理骰子失败", error);
    }
  }
  skipCurrent?.();
  skipCurrent = null;
  hideOverlay();
}

function abandonInitialization(): void {
  if (!abortBoxInit) return;
  const abort = abortBoxInit;
  abortBoxInit = null;
  boxGeneration += 1;
  boxInit = null;
  abort();
}
