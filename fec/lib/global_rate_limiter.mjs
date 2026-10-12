/**
 * Serialize request starts while allowing the requests themselves to overlap.
 * The injected clock/sleeper make the contract deterministic in tests.
 */
export function createGlobalRateLimiter(
  delayMs,
  {
    now = () => Date.now(),
    sleeper = (milliseconds) =>
      new Promise((resolve) => setTimeout(resolve, milliseconds)),
  } = {},
) {
  if (!Number.isFinite(delayMs) || delayMs < 0) {
    throw new Error("delayMs must be a non-negative finite number");
  }
  let nextStart = 0;
  let queue = Promise.resolve();
  return () => {
    const turn = queue.then(async () => {
      const waitMs = Math.max(0, nextStart - now());
      if (waitMs) await sleeper(waitMs);
      const startedAt = now();
      nextStart = startedAt + delayMs;
      return startedAt;
    });
    queue = turn.catch(() => {});
    return turn;
  };
}
