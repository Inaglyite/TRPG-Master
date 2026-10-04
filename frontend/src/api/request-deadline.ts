/** One budget covers headers and response bodies. Never retries a mutation. */
export class RequestDeadline {
  readonly controller = new AbortController();
  private readonly expired: Promise<never>;
  private readonly timer: ReturnType<typeof setTimeout>;

  constructor(milliseconds: number) {
    if (!Number.isFinite(milliseconds) || milliseconds <= 0)
      throw new RangeError(
        "A request deadline must be a positive finite duration",
      );
    let expire!: (error: DOMException) => void;
    this.expired = new Promise((_, reject) => {
      expire = reject;
    });
    this.timer = setTimeout(() => {
      const error = new DOMException(
        "Request deadline exceeded",
        "TimeoutError",
      );
      expire(error);
      this.controller.abort(error);
    }, milliseconds);
    // The budget may expire between awaited steps; later waits still reject,
    // without creating an unhandled rejection during that gap.
    void this.expired.catch(() => undefined);
  }

  wait<T>(operation: Promise<T>): Promise<T> {
    return Promise.race([operation, this.expired]);
  }

  dispose() {
    clearTimeout(this.timer);
  }
}
