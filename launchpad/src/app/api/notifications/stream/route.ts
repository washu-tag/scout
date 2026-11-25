import { NextRequest } from 'next/server';
import { ssoSessionActive } from '@/lib/keycloakToken';
import { database } from '@/lib/notifications/db';
import { notFound, reader, reauth, unavailable } from '@/lib/notifications/http';
import { noticeHub } from '@/lib/notifications/hub';
import { disabledSenders, notificationsEnabled } from '@/lib/notifications/settings';
import { noticeStore, type NoticeStore, type Summary } from '@/lib/notifications/store';
import { noticeStream } from '@/lib/notifications/stream';

export const dynamic = 'force-dynamic';

// Next answers HEAD with GET unless the route has its own, which would open a stream
// nobody reads.
export function HEAD() {
  return new Response(null, { status: 405, headers: { Allow: 'GET' } });
}

// Counts and a change marker only; the rail reads the notices themselves from
// GET /api/notifications.
export async function GET(req: NextRequest) {
  if (!notificationsEnabled()) return notFound();
  const session = await reader(req);
  if (!session) return reauth();
  const { jwt, username } = session;
  const hidden = disabledSenders();
  let store: NoticeStore;
  let first: Summary;
  try {
    store = noticeStore(await database());
    first = await store.summary(username, hidden);
  } catch (err) {
    return unavailable(err);
  }
  const stream = noticeStream({
    first,
    summary: () => store.summary(username, hidden),
    subscribe: (onChange) => noticeHub().subscribe(username, onChange),
    // A Keycloak that doesn't answer (null) leaves the stream open.
    sessionLive: async () =>
      (typeof jwt.exp !== 'number' || jwt.exp * 1000 > Date.now()) &&
      (await ssoSessionActive(jwt)) !== false,
    signal: req.signal,
  });
  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      // no-transform keeps compression from buffering the stream.
      'Cache-Control': 'no-cache, no-transform',
      'X-Accel-Buffering': 'no',
    },
  });
}
