import { NextResponse } from 'next/server';

export async function POST() {
  return NextResponse.json({ redirectUrl: process.env.OAUTH2_PROXY_SIGN_OUT_URL || null });
}
