const BASE_MS = 5_000;
const MAX_MS = 60_000;

// The wait before reconnecting after `attempt` failures in a row: half a window
// that doubles up to a minute, plus a random share of the other half, so tabs a
// launchpad rollout disconnects together don't all come back together.
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const window = Math.min(MAX_MS, BASE_MS * 2 ** attempt);
  return window / 2 + random() * (window / 2);
}
