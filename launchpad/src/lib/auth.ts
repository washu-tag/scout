import { NextAuthOptions } from 'next-auth';
import KeycloakProvider from 'next-auth/providers/keycloak';

export const authOptions: NextAuthOptions = {
  providers: [
    KeycloakProvider({
      clientId: process.env.KEYCLOAK_CLIENT_ID!,
      clientSecret: process.env.KEYCLOAK_CLIENT_SECRET!,
      issuer: process.env.KEYCLOAK_ISSUER!,
      authorization: { params: { scope: 'openid email profile microprofile-jwt' } },
    }),
  ],
  // Cap our next-auth session at the Keycloak SSO session lifetime (8h, set from
  // keycloak_token_lifespan via NEXTAUTH_SESSION_MAX_AGE). The default is 30 days,
  // which lets the cookie — and the refresh token it carries — far outlive the SSO
  // session it was minted from: the /api/users proxy can no longer refresh that
  // token, so every call 401s until cookies are cleared. Matching the lifetimes
  // also bounds how long the login-time isAdmin flag can stay stale after a
  // Keycloak change. (JWT strategy, since there is no database adapter; sessions
  // killed early — Keycloak redeploy, logout elsewhere — are recovered client-side
  // on the proxy 401, so this is the floor, not the only safeguard.)
  session: {
    strategy: 'jwt',
    maxAge: Number(process.env.NEXTAUTH_SESSION_MAX_AGE) || 28800,
  },
  callbacks: {
    async jwt({ token, account, profile }) {
      // Only the refresh token goes in the session cookie — NOT the access token.
      // An admin's access token carries dozens of roles and pushes the (JWE)
      // session cookie past the browser/proxy size limit, which intermittently
      // drops the session (phantom sign-outs). The /api/users proxy mints a fresh
      // access token from the refresh token per request instead.
      if (account) {
        token.refreshToken = account.refresh_token;
      }
      if (profile) {
        token.username = profile.preferred_username as string;
        // Resolve admin once at login; store the flag, not the (large) groups array.
        token.isAdmin = isAdminUser(profile.groups as string[]);
      }
      return token;
    },
    async session({ session, token }) {
      session.user.username = token.username as string;
      session.user.isAdmin = token.isAdmin as boolean;
      return session;
    },
  },
};

export function isAdminUser(groups?: string[]): boolean {
  if (!groups || groups.length === 0) return false;
  return groups.includes('launchpad-admin');
}
