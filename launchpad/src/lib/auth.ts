import { NextAuthOptions } from 'next-auth';
import KeycloakProvider from 'next-auth/providers/keycloak';
import { ADMIN_ROLE, uiRoles } from './roles';

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
        // Resolve roles once at login; store only the small filtered list the
        // UI gates on, not the (large) groups array.
        token.roles = uiRoles(profile.groups as string[]);
      }
      return token;
    },
    async session({ session, token }) {
      session.user.username = token.username as string;
      const roles = (token.roles as string[] | undefined) ?? [];
      session.user.roles = roles;
      session.user.isAdmin = roles.includes(ADMIN_ROLE);
      return session;
    },
  },
};
