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

// Both auth-failure responses below use 440 (Login Time-out), NOT 401. The shared
// `oauth2-proxy-error` Traefik middleware rewrites every upstream 401 into the
// oauth2-proxy sign-in page (status: ["401"] -> /oauth2/sign_in), which would
// swallow these app-level "re-authenticate me" signals before the launchpad UI
// ever sees them. 440 is caught by no middleware, so it reaches the browser
// intact. (Still never 403 — a genuine authz denial is the SPI's 403, forwarded
// as-is below.)
const REAUTH_REQUIRED = 440;

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
    // The refresh-token grant failed — almost always because the SSO session it
    // belonged to is gone (Keycloak redeploy, a logout elsewhere, or idle/max
    // timeout). This used to fail silently; log it so the cause is visible, and
    // let forward() turn it into a 401 the console renders as a re-login prompt.
    console.warn(
      `scout-users proxy: refresh-token grant failed (${res.status}) — SSO session likely expired`,
    );
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
    return NextResponse.json({ error: 'Not authenticated' }, { status: REAUTH_REQUIRED });
  }

  const body = method === 'POST' ? await req.text() : undefined;
  const accessToken = await freshAccessToken(jwt);
  if (!accessToken) {
    // Couldn't mint a token from the stored refresh token: the SSO session is
    // gone. Return REAUTH_REQUIRED (440, see above) so the console can prompt
    // re-login — a bare 401 would be rewritten into the oauth2-proxy sign-in page
    // by the ingress before the UI sees it.
    return NextResponse.json({ error: 'session_expired' }, { status: REAUTH_REQUIRED });
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
