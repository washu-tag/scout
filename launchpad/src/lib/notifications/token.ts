import { createRemoteJWKSet, errors, jwtVerify, type JWTVerifyGetKey } from 'jose';

// The launchpad client role a sender's service account holds (ADR 0043).
export const SEND_ROLE = 'notifications-send';

export type SenderToken =
  | { ok: true; clientId: string }
  | { ok: false; status: 401 | 403 | 503; error: string };

let remote: { url: string; keys: JWTVerifyGetKey } | null = null;

function realmKeys(issuer: string): JWTVerifyGetKey {
  const url = `${issuer}/protocol/openid-connect/certs`;
  if (remote?.url !== url) remote = { url, keys: createRemoteJWKSet(new URL(url)) };
  return remote.keys;
}

interface Claims {
  typ?: unknown;
  azp?: unknown;
  aud?: unknown;
  resource_access?: Record<string, { roles?: unknown } | undefined>;
}

// A Keycloak access token from the sender's own client (client-credentials
// grant), signed by the realm, holding the launchpad's send role and addressed
// to the launchpad. The sender is the client the token was issued to. The role
// is checked before the audience: Keycloak addresses a token to the launchpad
// only when it carries a launchpad role, so a missing role is the useful answer.
export async function verifySenderToken(
  authorization: string | null,
  { issuer, clientId, keys }: { issuer: string; clientId: string; keys?: JWTVerifyGetKey },
): Promise<SenderToken> {
  const token = authorization?.match(/^Bearer +([A-Za-z0-9._~+/-]+=*)$/)?.[1];
  if (!token) return { ok: false, status: 401, error: 'missing_token' };
  let claims: Claims;
  try {
    ({ payload: claims } = await jwtVerify<Claims>(token, keys ?? realmKeys(issuer), {
      issuer,
      clockTolerance: 30,
    }));
  } catch (err) {
    const unavailable =
      err instanceof errors.JWKSTimeout ||
      err instanceof errors.JWKSInvalid ||
      !(err instanceof errors.JOSEError);
    return unavailable
      ? { ok: false, status: 503, error: 'keys_unavailable' }
      : { ok: false, status: 401, error: 'invalid_token' };
  }
  if (claims.typ !== 'Bearer' || typeof claims.azp !== 'string' || !claims.azp) {
    return { ok: false, status: 401, error: 'invalid_token' };
  }
  const roles = claims.resource_access?.[clientId]?.roles;
  if (!Array.isArray(roles) || !roles.includes(SEND_ROLE)) {
    return { ok: false, status: 403, error: 'missing_role' };
  }
  const audience = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!audience.includes(clientId)) return { ok: false, status: 401, error: 'invalid_token' };
  return { ok: true, clientId: claims.azp };
}
