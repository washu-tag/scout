import { getToken } from 'next-auth/jwt';
import { NextRequest, NextResponse } from 'next/server';
import { REAUTH_REQUIRED, ssoSessionActive } from '@/lib/keycloakToken';

export const dynamic = 'force-dynamic';

// The shell polls this to learn that Scout's sessions ended while an app was
// open in a frame. A 401 never comes from here: it means the ingress session is
// gone and oauth2-proxy answered instead. A 503, which the shell ignores, means
// Keycloak didn't answer, so nobody is sent to sign in while it restarts.
export async function GET(req: NextRequest) {
  const jwt = await getToken({ req });
  const active = jwt ? await ssoSessionActive(jwt) : false;
  if (active === null) return new NextResponse(null, { status: 503 });
  if (!active) return new NextResponse(null, { status: REAUTH_REQUIRED });
  return new NextResponse(null, { status: 204 });
}
