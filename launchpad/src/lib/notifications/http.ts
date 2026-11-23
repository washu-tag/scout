import { getToken, type JWT } from 'next-auth/jwt';
import { NextRequest, NextResponse } from 'next/server';
import { REAUTH_REQUIRED } from '@/lib/keycloakToken';
import type { Reply } from './send';

export const NO_STORE = { 'Cache-Control': 'no-store' };

export function notFound(): NextResponse {
  return NextResponse.json({ error: 'Not found' }, { status: 404 });
}

export function reauth(): NextResponse {
  return NextResponse.json({ error: 'session_expired' }, { status: REAUTH_REQUIRED });
}

export function unavailable(err: unknown): NextResponse {
  console.error('[notifications] request failed', String(err));
  return NextResponse.json({ error: 'unavailable' }, { status: 503, headers: NO_STORE });
}

export function reply({ status, body, headers }: Reply): NextResponse {
  return NextResponse.json(body, { status, headers: { ...NO_STORE, ...headers } });
}

// The signed-in reader, from the launchpad session.
export async function reader(req: NextRequest): Promise<{ jwt: JWT; username: string } | null> {
  const jwt = await getToken({ req });
  const username = jwt?.username?.toLowerCase();
  return jwt && username ? { jwt, username } : null;
}
