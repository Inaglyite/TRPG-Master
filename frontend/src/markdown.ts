import { marked } from "marked";
import DOMPurify from "dompurify";

const renderer = new marked.Renderer();
renderer.image = () => "";
marked.setOptions({ breaks: true, gfm: true, renderer });

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text) as string, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "svg", "math", "iframe", "object", "embed", "form"],
    FORBID_ATTR: ["style"],
  });
}
