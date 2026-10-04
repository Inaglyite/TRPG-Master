import { useEffect, type RefObject } from "react";
import { HandoutImageViewer } from "../HandoutImageViewer";

export type ClueImageSelection = {
  source: string;
  label: string;
  scope: string;
};

/** Reading a currently projected image is not a reveal, grant or game action. */
export function ClueImagePreview({
  selection,
  scope,
  authorizedSources,
  returnFocus,
  onClose,
}: {
  selection: ClueImageSelection | null;
  scope: string;
  authorizedSources: readonly string[];
  returnFocus: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
}) {
  const allowed =
    selection !== null &&
    selection.scope === scope &&
    authorizedSources.includes(selection.source);
  // Hide during this render, then discard selection so a later regrant or
  // return to the old world cannot unexpectedly reopen an old picture.
  useEffect(() => {
    if (selection && !allowed) onClose();
  }, [selection, allowed, onClose]);
  if (!selection || !allowed) return null;
  return (
    <HandoutImageViewer
      key={selection.source}
      source={selection.source}
      label={selection.label}
      returnFocus={returnFocus}
      onClose={onClose}
    />
  );
}
