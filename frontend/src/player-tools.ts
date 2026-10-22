import type { ChatMessage } from "./state/message-store";
import type { DigestItem, PlayerTools } from "./api/playerTools";

/** Displayed, completed story only. No model history, hidden frames or failed drafts. */
export function currentTurnSource(
  messages: ChatMessage[],
  completedTurnId?: string | null,
) {
  const visible = messages.filter(
    (message) =>
      !message.hidden &&
      !(
        completedTurnId &&
        message.kind === "gm" &&
        message.turnId &&
        message.turnId !== completedTurnId
      ),
  );
  let last = visible.length - 1;
  while (
    last >= 0 &&
    !(
      visible[last].kind === "gm" &&
      !visible[last].streaming &&
      visible[last].text.trim()
    )
  )
    last--;
  if (last < 0) return null;
  const latest = visible[last];
  let start = last;
  while (start > 0) {
    const before = visible[start - 1];
    if (
      before.kind === "player" ||
      (latest.turnId && before.turnId && before.turnId !== latest.turnId)
    )
      break;
    start--;
  }
  const story = visible
    .slice(start, last + 1)
    .filter((message) => message.kind === "gm" && !message.streaming);
  const text = story
    .map((message) =>
      message.segments?.length
        ? message.segments
            .map(
              (segment) =>
                `${segment.speaker?.name ? `${segment.speaker.name}：` : ""}${segment.text}`,
            )
            .join("\n\n")
        : message.text,
    )
    .join("\n\n")
    .trim();
  const turnIds = [
    ...new Set(
      messages
        .slice(0, messages.indexOf(latest) + 1)
        .filter((message) => !message.hidden)
        .filter((m) => m.kind === "gm")
        .map((m) => m.turnId || m.id),
    ),
  ];
  return text
    ? {
        id: latest.turnId || latest.id,
        text,
        label: latest.turnId
          ? `第 ${turnIds.length} 轮叙述`
          : "最近一段公开叙述",
      }
    : null;
}

export function digestNote(source: { label: string }, items: DigestItem[]) {
  return `### 探案摘录 · ${source.label}\n\n${items.map((item) => `- ${item.text.trim()}\n  原文：${item.quote.replace(/\n/g, " ")}`).join("\n\n")}`;
}

/** Layout depends exclusively on the already-filtered player graph. */
export function mapLayout(map: PlayerTools["map"]) {
  const nodes = [...map.nodes].sort(
    (a, b) =>
      Number(b.current) - Number(a.current) ||
      a.name.localeCompare(b.name, "zh-CN"),
  );
  const columns =
    nodes.length > 1 ? Math.min(3, Math.ceil(Math.sqrt(nodes.length))) : 1;
  return {
    width: columns * 220,
    height: Math.max(1, Math.ceil(nodes.length / columns)) * 135,
    nodes: nodes.map((node, index) => ({
      ...node,
      x: (index % columns) * 220 + 110,
      y: Math.floor(index / columns) * 135 + 67.5,
    })),
  };
}
