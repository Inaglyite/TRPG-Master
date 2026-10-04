export const NOTES_RESPONSE_TIMEOUT_MS = 15000;

export type NotesScope = { worldId: string; ownerKey: string };
export type NotesOperation = {
  requestId: string;
  scope: NotesScope;
  kind: "read" | "save";
  submittedText?: string;
};

/** Correlation and deadlines only. Never sends, retries, saves, or changes a world. */
export class NotesRequests {
  current: NotesOperation | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly onTimeout: (operation: NotesOperation) => void,
  ) {}

  begin(
    scope: NotesScope,
    kind: NotesOperation["kind"],
    submittedText?: string,
  ) {
    this.cancel();
    const operation: NotesOperation = {
      requestId: crypto.randomUUID(),
      scope: { ...scope },
      kind,
      submittedText,
    };
    this.current = operation;
    this.timer = setTimeout(() => {
      if (this.current !== operation) return;
      this.current = null;
      this.timer = null;
      this.onTimeout(operation);
    }, NOTES_RESPONSE_TIMEOUT_MS);
    return operation;
  }

  matches(
    scope: NotesScope,
    data: { request_id?: unknown; world_id?: unknown; saved?: unknown },
    failure = false,
  ) {
    const operation = this.current;
    if (
      !operation ||
      operation.scope.ownerKey !== scope.ownerKey ||
      operation.scope.worldId !== scope.worldId
    )
      return false;
    if (data.world_id !== undefined && data.world_id !== scope.worldId)
      return false;
    if (
      data.request_id !== undefined &&
      data.request_id !== operation.requestId
    )
      return false;
    // Compatibility with old servers without correlation fields remains explicit:
    // a read payload must still never acknowledge a pending save.
    if (!failure && operation.kind === "save" && data.saved !== true)
      return false;
    return true;
  }

  finish() {
    const operation = this.current;
    this.cancel();
    return operation;
  }

  cancel() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.current = null;
  }
}
