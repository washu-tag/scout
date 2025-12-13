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
      if (account) {
        token.accessToken = account.access_token;
        token.refreshToken = account.refresh_token;
      }
      if (profile) {
        token.username = profile.preferred_username as string;
        token.groups = profile.groups as string[];
      }
      return token;
    },
    async session({ session, token }) {
      session.user.username = token.username as string;
      session.user.groups = token.groups as string[];
      // Add admin role to session based on Keycloak groups
      session.user.isAdmin = isAdminUser(token.groups as string[]);
      return session;
    },
  },
};

export function isAdminUser(groups?: string[]): boolean {
  if (!groups || groups.length === 0) return false;
  return groups.includes('launchpad-admin');
}
