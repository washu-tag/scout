// Runs once as the server starts: brings the notifications schema up to date
// (ADR 0043) when the launchpad has a database.
export async function register() {
  if (process.env.NEXT_RUNTIME !== 'nodejs') return;
  const { startNotifications } = await import('@/lib/notifications/db');
  startNotifications();
}
