import { getToken, type JWT } from 'next-auth/jwt';
import { NextRequest, NextResponse } from 'next/server';

// Server-side proxy to the Keycloak scout-approval REST API. The browser never
// holds the Keycloak token: we read the caller's access token from their
// next-auth session (server-side) and forward it as a Bearer to Keycloak in the
// same realm, so the page makes no cross-origin call. Keycloak still enforces
// scout-admin on every endpoint (defense in depth) — this proxy only forwards.

const ALLOWED: Record<string, readonly string[]> = {
  GET: ['schema', 'pending'],
  POST: ['approve'],
};

// Keycloak access tokens are short-lived (realm accessTokenLifespan, 300s), so
// refresh against the stored refresh token when the cached one is expired (or
// about to be). Refresh-token rotation is off in the realm, so the stored
// refresh token stays reusable for the SSO session — we don't need to persist a
// rotated value back into the session cookie.
async function freshAccessToken(jwt: JWT, force: boolean): Promise<string | null> {
  const now = Math.floor(Date.now() / 1000);
  if (!force && jwt.accessToken && jwt.expiresAt && jwt.expiresAt - 15 > now) {
    return jwt.accessToken;
  }
  if (!jwt.refreshToken) {
    return jwt.accessToken ?? null;
  }
  const res = await fetch(`${process.env.KEYCLOAK_ISSUER}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: process.env.KEYCLOAK_CLIENT_ID ?? '',
      client_secret: process.env.KEYCLOAK_CLIENT_SECRET ?? '',
      refresh_token: jwt.refreshToken,
    }),
  });
  if (!res.ok) {
    return null;
  }
  const data = (await res.json()) as { access_token?: string };
  return data.access_token ?? null;
}

function callKeycloak(
  issuer: string,
  action: string,
  method: string,
  accessToken: string,
  body?: string,
) {
  const init: RequestInit = {
    method,
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
  };
  if (body !== undefined) {
    init.body = body;
  }
  return fetch(`${issuer}/scout-approval/${action}`, init);
}

async function forward(req: NextRequest, action: string, method: 'GET' | 'POST') {
  if (!ALLOWED[method].includes(action)) {
    return NextResponse.json({ error: 'Not found' }, { status: 404 });
  }
  const issuer = process.env.KEYCLOAK_ISSUER;
  if (!issuer) {
    return NextResponse.json({ error: 'KEYCLOAK_ISSUER is not configured' }, { status: 500 });
  }
  const jwt = await getToken({ req });
  if (!jwt) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  const body = method === 'POST' ? await req.text() : undefined;
  let accessToken = await freshAccessToken(jwt, false);
  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  let upstream = await callKeycloak(issuer, action, method, accessToken, body);
  if (upstream.status === 401) {
    // Cached token rejected (clock skew / mid-flight expiry): force one refresh
    // and retry before giving up.
    accessToken = await freshAccessToken(jwt, true);
    if (accessToken) {
      upstream = await callKeycloak(issuer, action, method, accessToken, body);
    }
  }

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
  });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  return forward(req, action, 'GET');
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  return forward(req, action, 'POST');
}
