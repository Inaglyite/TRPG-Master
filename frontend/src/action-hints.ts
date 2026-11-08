/** Hide generated menus only in presentation copies, preserving subsequent story. */
export function withoutActionHints(text: string): string {
  const heading =
    /^\s*(?:[>#＃]+\s*)?[*_`]*(?:你可以(?:选择)?|接下来你可以|可选行动|请选择)\s*[*_`]*(?:[—–－─━―\-:：]+[*_`：:\s]*|\s*)$/;
  const item = /^\s*(?:\d+[.．、)）]|[①②③④⑤⑥⑦⑧])\s*/;
  const lines: string[] = [];
  let inMenu = false;
  for (const line of text.split("\n")) {
    if (heading.test(line)) {
      inMenu = true;
      continue;
    }
    if (inMenu) {
      if (!line.trim() || item.test(line) || /^\s{2,}\S/.test(line)) continue;
      inMenu = false;
    }
    lines.push(line);
  }
  return lines.join("\n").trim();
}
