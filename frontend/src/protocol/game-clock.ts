/** Numeric authority only: no prose/time keyword extraction or wall-clock ticks. */
export function readGameMinutes(value: unknown): number | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => key !== "elapsed_minutes")) return null;
  const minutes = record.elapsed_minutes;
  return typeof minutes === "number" &&
    Number.isSafeInteger(minutes) &&
    minutes >= 0
    ? minutes
    : null;
}

export function elapsedGameTime(minutes: number | null): string {
  if (minutes === null || !Number.isSafeInteger(minutes) || minutes < 0)
    return "未提供";
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const remaining = minutes % 60;
  return `${days ? `${days}天` : ""}${hours ? `${hours}小时` : ""}${remaining || (!days && !hours) ? `${remaining}分钟` : ""}`;
}
