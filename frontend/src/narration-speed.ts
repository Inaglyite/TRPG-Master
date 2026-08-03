/**
 * 叙述播放速度偏好：三档、客户端本地持久化，单机与联机共用。
 * 无效或旧存储值一律回退到"标准"。
 */

export type NarrationSpeed = "slow" | "standard" | "fast";

const STORAGE_KEY = "trpg-narration-speed";

const CHARS_PER_SECOND: Record<NarrationSpeed, number> = {
  slow: 18,
  standard: 28,
  fast: 42,
};

export const NARRATION_SPEED_OPTIONS: {
  value: NarrationSpeed;
  label: string;
  hint: string;
}[] = [
  { value: "slow", label: "慢", hint: "约 18 字/秒，更强调阅读和氛围" },
  { value: "standard", label: "标准", hint: "约 28 字/秒" },
  { value: "fast", label: "快", hint: "约 42 字/秒，熟练玩家" },
];

function isNarrationSpeed(value: unknown): value is NarrationSpeed {
  return value === "slow" || value === "standard" || value === "fast";
}

export function getNarrationSpeed(): NarrationSpeed {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isNarrationSpeed(stored)) return stored;
  } catch {
    /* localStorage 不可用时回退标准 */
  }
  return "standard";
}

export function setNarrationSpeed(speed: NarrationSpeed): void {
  if (!isNarrationSpeed(speed)) return;
  try {
    localStorage.setItem(STORAGE_KEY, speed);
  } catch {
    /* 写入失败不影响本回合 */
  }
}

/** 单字基础节拍（毫秒/字）；标点与说话人切换停顿在此基础上另行计算。 */
export function narrationTickMs(speed: NarrationSpeed): number {
  return 1000 / CHARS_PER_SECOND[speed];
}

/** 按住叙述区域达到该时长后进入临时加速；松开/移出/取消/失焦恢复。 */
export const NARRATION_LONG_PRESS_MS = 250;
