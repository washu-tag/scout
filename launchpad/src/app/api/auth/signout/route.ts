import { NextRequest, NextResponse } from 'next/server';
import { getToken } from 'next-auth/jwt';

export async function POST(req: NextRequest) {
  const baseUrl = process.env.OAUTH2_PROXY_SIGN_OUT_URL;
  if (!baseUrl) {
    return NextResponse.json({ redirectUrl: null });
  }
  const token = await getToken({ req });
  const url = new URL(baseUrl);
  if (token?.idToken) {
    url.searchParams.set('id_token_hint', token.idToken);
  }
  return NextResponse.json({ redirectUrl: url.toString() });
}
