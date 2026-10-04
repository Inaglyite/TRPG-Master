// A dependency-free generation counter shared by the HTTP client and the
// authentication state machine. Comparing origins alone misses A → B → A.
let cloudRequestGeneration = 0;
const listeners = new Set<() => void>();

export function subscribeCloudRequests(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function currentCloudRequestGeneration(): number {
  return cloudRequestGeneration;
}

export function invalidateCloudRequests(): void {
  cloudRequestGeneration += 1;
  for (const listener of listeners) listener();
}
