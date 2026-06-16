import { NextResponse } from 'next/server';

// Hands the client the oauth2-proxy (ingress) sign-out URL so the dropdown can
// redirect there after next-auth has cleared its own session.
//
// Deliberately NOT under /api/auth: a route there shadows next-auth's own
// /api/auth/signout (App Router static segments beat the [...nextauth] catch-all),
// so signOut() never reaches next-auth's handler — and since the session cookie
// is httpOnly, only that handler can expire it. Living here keeps next-auth's
// signout intact, so the launchpad logout actually clears the next-auth cookie.
export async function POST() {
  return NextResponse.json({ redirectUrl: process.env.OAUTH2_PROXY_SIGN_OUT_URL || null });
}
