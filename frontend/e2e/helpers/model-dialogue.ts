/** Match the real provider wire format while preserving legacy fixture text. */
export function modelDialogueContent(body: string, text: string): string {
  const request = JSON.parse(body || "{}");
  if (request.response_format?.type !== "json_object") return text;
  const [narrative, menu] = text.split(/\n\n\*\*你可以——\*\*\n/);
  return JSON.stringify({
    segments: [{ kind: "narration", text: narrative }],
    introduced_npc_ids: [],
    choices: menu
      ? menu.split("\n").map((line) => line.replace(/^\d+\.\s*/, ""))
      : [],
  });
}
