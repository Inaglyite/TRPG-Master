import { useEffect, type RefObject } from "react";

import { focusableControls, trapDialogTab } from "./dialogFocus";

/** Focus only; never interprets player text or submits an action. */
export function useDialogKeyboard(
  panel: RefObject<HTMLElement | null>,
  open: boolean,
  busy: boolean,
  close: () => void,
  returnFocus?: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    const root = panel.current;
    if (root) (focusableControls(root)[0] || root).focus();
    return () => {
      const target = returnFocus?.current || previous;
      if (target instanceof HTMLElement && target.isConnected) target.focus();
    };
  }, [open, panel, returnFocus]);

  useEffect(() => {
    if (!open) return;
    const root = panel.current;
    if (
      root &&
      (document.activeElement === root ||
        document.activeElement === document.body ||
        (root.contains(document.activeElement) &&
          document.activeElement?.matches(":disabled")))
    ) {
      (focusableControls(root)[0] || root).focus();
    }
    const listener = (event: KeyboardEvent) => {
      const root = panel.current;
      if (!root || event.isComposing || !root.contains(document.activeElement))
        return;
      trapDialogTab(event, root);
      if (event.key === "Escape") {
        // Explicit nested editors/confirmations get first refusal. Their own
        // handlers consume Escape; they cannot turn it into a game action.
        if (
          document.activeElement instanceof HTMLElement &&
          document.activeElement.closest("[data-dialog-escape]")
        )
          return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!busy) close();
      }
    };
    document.addEventListener("keydown", listener, true);
    let focusFrame: number | null = null;
    const revealFocus = (event: FocusEvent) => {
      const control = event.target;
      if (!(control instanceof HTMLElement)) return;
      const scrollBody = control.closest<HTMLElement>("[data-dialog-scroll]");
      if (!scrollBody || !root?.contains(scrollBody)) return;
      if (focusFrame !== null) window.cancelAnimationFrame(focusFrame);
      // Native focus may scroll only the field centre into view. Run after it,
      // inside the dialog body only; never scroll the underlying playfield.
      focusFrame = window.requestAnimationFrame(() => {
        focusFrame = null;
        if (!control.isConnected || document.activeElement !== control) return;
        const body = scrollBody.getBoundingClientRect();
        const field = control.getBoundingClientRect();
        if (!body.height || !field.height) return;
        const inset = body.height >= field.height + 16 ? 8 : 0;
        if (field.bottom > body.bottom - inset)
          scrollBody.scrollTop += field.bottom - body.bottom + inset;
        else if (field.top < body.top + inset)
          scrollBody.scrollTop -= body.top + inset - field.top;
      });
    };
    root?.addEventListener("focusin", revealFocus);
    // Inline editors and switched pages may remove the focused node without
    // changing this hook's dependencies. Recover only orphaned body focus,
    // after React's commit/autofocus, leaving explicit focus elsewhere alone.
    const observer = new MutationObserver(() => {
      if (root?.isConnected && document.activeElement === document.body)
        (focusableControls(root)[0] || root).focus();
    });
    if (root) observer.observe(root, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      document.removeEventListener("keydown", listener, true);
      root?.removeEventListener("focusin", revealFocus);
      if (focusFrame !== null) window.cancelAnimationFrame(focusFrame);
    };
  }, [open, busy, close, panel]);
}
