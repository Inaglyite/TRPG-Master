import { elapsedGameTime } from "../../../protocol/game-clock";
import { useAppStore } from "../../../state/app-store";
import { useStructuredStore } from "../../../state/structured-store";

export function GameClockReadout({
  variant,
}: {
  variant: "header" | "reference";
}) {
  const minutes = useStructuredStore((s) => s.clockMinutes);
  const connection = useAppStore((s) => s.connection);
  const elapsed = elapsedGameTime(minutes);
  const text =
    connection !== "connected"
      ? elapsed === "未提供"
        ? "同步中"
        : `同步中，上次记录${elapsed}`
      : elapsed === "未提供"
        ? elapsed
        : `已过${elapsed}`;
  return (
    <div
      className={`game-clock-readout game-clock-${variant}`}
      data-testid={`${variant}-game-clock`}
      title={`游戏时间：${text}`}
    >
      <span className="game-clock-label">游戏时间 · </span>
      <span>{text}</span>
    </div>
  );
}
