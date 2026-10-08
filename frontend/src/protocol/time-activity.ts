/** Canonical wire types; descriptions must never be parsed into activity. */
export const TIME_ACTIVITIES = [
  "wait",
  "travel",
  "check",
  "interact",
  "combat",
  "other",
] as const;

export const TIME_ACTIVITY_LABELS: Record<string, string> = {
  wait: "等待／休整",
  travel: "赶路（仅计时）",
  check: "检定耗时（仅计时）",
  interact: "交谈／现场活动",
  combat: "战斗耗时（仅计时）",
  other: "其他（不按等待计时）",
};
