import { NextRequest, NextResponse } from 'next/server';

// Override NextAuth's default error page to redirect to home
// HomeClient's auto-login will then handle re-authentication
// Use a cookie to track retries and prevent infinite loops on real auth errors
export async function GET(request: NextRequest) {
  const hasRetried = request.cookies.get('auth_retry');
  // Use NEXTAUTH_URL for the external-facing URL
  const baseUrl = process.env.NEXTAUTH_URL || request.nextUrl.origin;

  if (hasRetried) {
    // Already tried once - clear cookie and show error page to break the loop
    const response = NextResponse.redirect(new URL('/auth-error', baseUrl));
    response.cookies.delete('auth_retry');
    return response;
  }

  // First error - set retry cookie and redirect to home
  const response = NextResponse.redirect(new URL('/', baseUrl));
  response.cookies.set('auth_retry', '1', {
    maxAge: 60, // Cookie expires in 60 seconds
    httpOnly: true,
    sameSite: 'lax',
  });
  return response;
}
