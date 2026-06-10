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
