import { getServerSession } from 'next-auth/next'
import { NextAuthOptions } from 'next-auth'
import GitHubProvider from 'next-auth/providers/github'

export const authOptions: NextAuthOptions = {
  providers: [
    GitHubProvider({
      clientId: process.env.GITHUB_CLIENT_ID!,
      clientSecret: process.env.GITHUB_CLIENT_SECRET!,
    }),
  ],
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account) {
        token.accessToken = account.access_token
      }
      if (profile && 'login' in profile) {
        token.username = profile.login as string
      }
      return token
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string
      session.user.username = token.username as string
      // Add admin role to session (server-side only)
      session.user.isAdmin = isAdminUser(token.username as string)
      return session
    },
  },
}

export async function getSession() {
  return await getServerSession(authOptions)
}

export function isAdminUser(username?: string): boolean {
  if (!username) return false
  const adminUsers = process.env.ADMIN_USERS?.split(',').map(u => u.trim()) || []
  return adminUsers.includes(username)
}