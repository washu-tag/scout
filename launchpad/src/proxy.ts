import { NextRequest, NextResponse } from 'next/server';
import { contentSecurityPolicy } from '@/lib/csp';

// A fresh nonce per page request; Next.js reads it from the request's policy and
// puts it on the scripts it renders.
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');
  const host = request.headers.get('x-forwarded-host') ?? request.headers.get('host') ?? '';
  const policy = contentSecurityPolicy({
    nonce,
    host,
    dev: process.env.NODE_ENV === 'development',
  });
  const headers = new Headers(request.headers);
  headers.set('x-nonce', nonce);
  headers.set('Content-Security-Policy', policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set('Content-Security-Policy', policy);
  return response;
}

// API routes return JSON, and /open sets its own policy for its one inline script.
export const config = {
  matcher: [
    {
      source: '/((?!api/|open/|_next/static|_next/image|favicon\\.ico|scout\\.svg|scout\\.png).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
};
