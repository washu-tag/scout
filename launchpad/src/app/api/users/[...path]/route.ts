import { getToken, type JWT } from 'next-auth/jwt';
import { NextRequest, NextResponse } from 'next/server';

// Server-side proxy to the Keycloak scout-users REST API. The browser never
// holds the Keycloak token: we read the caller's access token from their
// next-auth session (server-side) and forward it as a Bearer to Keycloak in the
// same realm, so the page makes no cross-origin call. Keycloak still enforces
// scout-admin on every endpoint (defense in depth) — this proxy only forwards.
//
// Catch-all so the sub-resourced paths (users/{id}/attributes, users/{id}/admin,
// users/{id}/membership) match alongside the flat ones (schema, pending, users,
// approve). Each method's allowlist of path shapes is checked before forwarding.

const ALLOWED: Record<string, readonly RegExp[]> = {
  GET: [/^schema$/, /^pending$/, /^users$/],
  POST: [/^approve$/, /^users\/[^/]+\/attributes$/, /^users\/[^/]+\/admin$/],
  DELETE: [/^users\/[^/]+\/(admin|membership)$/],
};

// Mint a fresh access token from the refresh token on every request. The session
// cookie holds only the refresh token (not the access token) to keep it small
// enough that the browser/proxy doesn't drop it (see auth.ts), so there's nothing
// to cache. Refresh-token rotation is off in the realm, so the refresh token
// stays reusable for the SSO session.
async function freshAccessToken(jwt: JWT): Promise<string | null> {
  if (!jwt.refreshToken) {
    return null;
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
  path: string,
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
  return fetch(`${issuer}/scout-users/${path}`, init);
}

async function forward(req: NextRequest, segments: string[], method: 'GET' | 'POST' | 'DELETE') {
  const path = (segments ?? []).join('/');
  if (!path || !ALLOWED[method].some((re) => re.test(path))) {
    return NextResponse.json({ error: 'Not found' }, { status: 404 });
  }
  // Allowlist matches the path only; forward the query string (?status=&search=) too,
  // or the SPI's status/search filters would silently receive nothing.
  const target = path + req.nextUrl.search;
  const issuer = process.env.KEYCLOAK_ISSUER;
  if (!issuer) {
    return NextResponse.json({ error: 'KEYCLOAK_ISSUER is not configured' }, { status: 500 });
  }
  const jwt = await getToken({ req });
  if (!jwt) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  const body = method === 'POST' ? await req.text() : undefined;
  const accessToken = await freshAccessToken(jwt);
  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  const upstream = await callKeycloak(issuer, target, method, accessToken, body);

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
  });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return forward(req, path, 'GET');
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return forward(req, path, 'POST');
}

export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return forward(req, path, 'DELETE');
}
