import type { JWT } from 'next-auth/jwt';

// Login Time-out, not 401: the shared `oauth2-proxy-error` Traefik middleware
// rewrites every upstream 401 into the oauth2-proxy sign-in page, which would
// swallow this "re-authenticate me" signal before the launchpad UI sees it.
export const REAUTH_REQUIRED = 440;

function tokenRequest(path: string, params: Record<string, string>) {
  return fetch(`${process.env.KEYCLOAK_ISSUER}/protocol/openid-connect/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: process.env.KEYCLOAK_CLIENT_ID ?? '',
      client_secret: process.env.KEYCLOAK_CLIENT_SECRET ?? '',
      ...params,
    }),
  });
}

// Mint a fresh access token from the refresh token. The session cookie holds
// only the refresh token (not the access token) to keep it small enough that
// the browser/proxy doesn't drop it (see auth.ts), so there's nothing to cache.
// Refresh-token rotation is off in the realm, so the refresh token stays
// reusable for the SSO session.
export async function freshAccessToken(jwt: JWT): Promise<string | null> {
  if (!jwt.refreshToken) {
    return null;
  }
  const res = await tokenRequest('token', {
    grant_type: 'refresh_token',
    refresh_token: jwt.refreshToken,
  });
  if (!res.ok) {
    // Almost always because the SSO session it belonged to is gone (Keycloak
    // redeploy, a logout elsewhere, or idle/max timeout).
    console.warn(`refresh-token grant failed (${res.status}) — SSO session likely expired`);
    return null;
  }
  const data = (await res.json()) as { access_token?: string };
  return data.access_token ?? null;
}

// Whether the SSO session behind the refresh token is still alive. Introspection
// rather than a refresh grant, so a periodic check doesn't reset Keycloak's idle
// timeout.
export async function ssoSessionActive(jwt: JWT): Promise<boolean> {
  if (!jwt.refreshToken) {
    return false;
  }
  const res = await tokenRequest('token/introspect', {
    token: jwt.refreshToken,
    token_type_hint: 'refresh_token',
  });
  if (!res.ok) {
    return false;
  }
  const data = (await res.json()) as { active?: boolean };
  return data.active === true;
}
