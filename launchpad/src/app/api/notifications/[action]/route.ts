import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { database } from '@/lib/notifications/db';
import { NO_STORE, notFound, reader, reauth, unavailable } from '@/lib/notifications/http';
import { disabledSenders, notificationsEnabled } from '@/lib/notifications/settings';
import { noticeStore, type MarkAction } from '@/lib/notifications/store';

export const dynamic = 'force-dynamic';

const ACTIONS = new Set<string>(['seen', 'read', 'archive']);

const bodySchema = z.union([
  z.strictObject({ ids: z.array(z.uuid()).min(1).max(500) }),
  z.strictObject({ all: z.literal(true) }),
]);

// Marks the signed-in user's own notices seen, read or archived; `read` also
// takes {"all": true}. JSON only, so another Scout origin can't post a form here.
export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (!notificationsEnabled() || !ACTIONS.has(action)) return notFound();
  if (!req.headers.get('content-type')?.startsWith('application/json')) {
    return NextResponse.json({ error: 'unsupported_media_type' }, { status: 415 });
  }
  const session = await reader(req);
  if (!session) return reauth();
  const parsed = bodySchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success || ('all' in parsed.data && action !== 'read')) {
    return NextResponse.json({ error: 'invalid_request' }, { status: 400 });
  }
  try {
    const store = noticeStore(await database());
    await store.mark(
      session.username,
      action as MarkAction,
      'all' in parsed.data ? 'all' : parsed.data.ids,
    );
    return NextResponse.json(await store.summary(session.username, disabledSenders()), {
      headers: NO_STORE,
    });
  } catch (err) {
    return unavailable(err);
  }
}
