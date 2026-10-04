/** DOM focus rules shared by game overlays; never infer or execute game actions. */
const FOCUSABLE =
  'button,input,select,textarea,a[href],summary,[tabindex]:not([tabindex="-1"])';

export function focusableControls(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (node) => {
      if (
        node.tabIndex < 0 ||
        node.matches(":disabled") ||
        node.closest('[hidden],[inert],[aria-hidden="true"]')
      )
        return false;
      for (
        let parent: HTMLElement | null = node;
        parent && parent !== root;
        parent = parent.parentElement
      ) {
        const style = getComputedStyle(parent);
        if (style.display === "none" || style.visibility === "hidden")
          return false;
        if (parent instanceof HTMLDetailsElement && !parent.open) {
          const summary = Array.from(parent.children).find(
            (child) => child.tagName === "SUMMARY",
          );
          if (!summary?.contains(node)) return false;
        }
      }
      return true;
    },
  );
}

export function trapDialogTab(
  event: { key: string; shiftKey: boolean; preventDefault: () => void },
  root: HTMLElement,
): void {
  if (event.key !== "Tab") return;
  const controls = focusableControls(root);
  const first = controls[0];
  const last = controls.at(-1);
  const active = document.activeElement;
  if (!first || !last) {
    event.preventDefault();
    root.focus();
  } else if (
    event.shiftKey &&
    (active === first || active === root || !root.contains(active))
  ) {
    event.preventDefault();
    last.focus();
  } else if (
    !event.shiftKey &&
    (active === last || active === root || !root.contains(active))
  ) {
    event.preventDefault();
    first.focus();
  }
}
