import { NextRequest, NextResponse } from 'next/server';
import { database } from '@/lib/notifications/db';
import { NO_STORE, notFound, reader, reauth, reply, unavailable } from '@/lib/notifications/http';
import { defaultSendDeps, sendNotice, withdrawNotice } from '@/lib/notifications/send';
import { disabledSenders, notificationsEnabled } from '@/lib/notifications/settings';
import { noticeStore } from '@/lib/notifications/store';

export const dynamic = 'force-dynamic';

// The signed-in user's own notices (ADR 0043).
export async function GET(req: NextRequest) {
  if (!notificationsEnabled()) return notFound();
  const session = await reader(req);
  if (!session) return reauth();
  try {
    const store = noticeStore(await database());
    const hidden = disabledSenders();
    const [notices, summary] = await Promise.all([
      store.list(session.username, hidden),
      store.summary(session.username, hidden),
    ]);
    return NextResponse.json({ notices, ...summary }, { headers: NO_STORE });
  } catch (err) {
    return unavailable(err);
  }
}

// A registered app sends a notice, authenticated by its own client's token.
export async function POST(req: NextRequest) {
  if (!notificationsEnabled()) return notFound();
  return reply(
    await sendNotice(req.headers.get('authorization'), () => req.text(), defaultSendDeps()),
  );
}

// The app withdraws its notice with ?collapseKey= from everyone it reached.
export async function DELETE(req: NextRequest) {
  if (!notificationsEnabled()) return notFound();
  return reply(
    await withdrawNotice(
      req.headers.get('authorization'),
      req.nextUrl.searchParams.get('collapseKey'),
      defaultSendDeps(),
    ),
  );
}
