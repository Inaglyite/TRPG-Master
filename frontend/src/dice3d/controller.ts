import type { VisualDie } from "../state/message-store";

/**
 * 3D 骰控制器：懒加载 @3d-dice/dice-box-threejs（three+cannon-es 经动态
 * import 进入独立 chunk，不进主包），单例 DiceBox 附着在聊天面板的
 * 覆盖层上。所有路径都有权威文字结果回退：
 * reduced-motion、无 WebGL、初始化失败、非标准面数、忙线并发。
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
const ROLL_TIMEOUT_MS = 5000;

let box: DiceBoxLike | null = null;
let boxInit: Promise<DiceBoxLike | null> | null = null;
let boxFailed = false;
let overlayEl: HTMLDivElement | null = null;
let rolling = false;
let rollSeq = 0;
let skipCurrent: (() => void) | null = null;
let boxGeneration = 0;

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
  try {
    instance.clearDice();
    instance.renderer?.dispose?.();
    instance.renderer?.forceContextLoss?.();
    instance.renderer?.domElement?.remove();
  } catch (error) {
    console.warn("释放 3D 骰资源失败", error);
  }
}

function ensureOverlay(): HTMLDivElement {
  if (overlayEl) return overlayEl;
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

async function ensureBox(): Promise<DiceBoxLike | null> {
  if (box) return box;
  if (boxFailed) return null;
  if (!boxInit) {
    const generation = boxGeneration;
    boxInit = (async () => {
      try {
        const { default: DiceBox } = await import("@3d-dice/dice-box-threejs");
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
        await instance.initialize();
        if (generation !== boxGeneration) {
          disposeBox(instance as DiceBoxLike);
          return null;
        }
        box = instance as DiceBoxLike;
        return box;
      } catch (error) {
        if (generation === boxGeneration) {
          console.warn("3D 骰初始化失败，回退文字结果", error);
          hideOverlay();
          boxFailed = true;
        }
        return null;
      }
    })();
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
 * 投掷一组 3D 骰。resolve 时机：物理定格、超时兜底或用户点击跳过。
 * reject 时机：3D 不可用（初始化失败/记法不支持/忙线）——调用方回退 CSS。
 */
export async function rollDice3D(dice: VisualDie[]): Promise<void> {
  const notation = toNotation(dice);
  if (!notation || rolling) throw new Error("dice3d unavailable");
  // 在第一个 await 之前占位，避免两条消息同时穿过 busy 检查。
  rolling = true;
  const seq = ++rollSeq;
  try {
    const instance = await ensureBox();
    if (!instance || seq !== rollSeq) throw new Error("dice3d init cancelled");
    const activeInstance = instance;
    showOverlay();
    await new Promise<void>((resolve) => {
      let finished = false;
      const timer = window.setTimeout(() => finish(true), ROLL_TIMEOUT_MS);
      skipCurrent = () => finish(true);
      function finish(clear = false) {
        if (finished) return;
        finished = true;
        if (clear) activeInstance.clearDice();
        window.clearTimeout(timer);
        skipCurrent = null;
        resolve();
      }
      activeInstance.roll(notation).then(
        () => finish(),
        () => finish(true),
      );
    });
  } finally {
    if (seq === rollSeq) {
      rolling = false;
      hideOverlay();
    }
  }
}

/** 组件卸载时调用：立即收起覆盖层并让等待中的 Promise 落地。 */
export function cancelDice3D(): void {
  rollSeq += 1;
  rolling = false;
  box?.clearDice();
  skipCurrent?.();
  skipCurrent = null;
  hideOverlay();
}
