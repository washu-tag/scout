// Site settings for notifications (ADR 0043). Notifications are on only when the
// launchpad has a database.

export function databaseUrl(): string | undefined {
  return process.env.LAUNCHPAD_DATABASE_URL || undefined;
}

export function notificationsEnabled(): boolean {
  return databaseUrl() !== undefined;
}

function list(value: string | undefined, separator: string): string[] {
  return (value ?? '')
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}

function positiveInteger(value: string | undefined, fallback: number): number {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

export function senderDirs(): string[] {
  return list(process.env.LAUNCHPAD_NOTIFICATION_SENDER_DIRS, ':');
}

export function disabledSenders(): string[] {
  return list(process.env.LAUNCHPAD_NOTIFICATION_DISABLED_SENDERS, ',');
}

// Sends and withdrawals per sender per minute, across all replicas.
export function sendRateLimit(): number {
  return positiveInteger(process.env.LAUNCHPAD_NOTIFICATION_RATE_LIMIT, 60);
}

// Deliveries one notice may have once its roles are expanded.
export function maxDeliveries(): number {
  return positiveInteger(process.env.LAUNCHPAD_NOTIFICATION_MAX_DELIVERIES, 1000);
}
