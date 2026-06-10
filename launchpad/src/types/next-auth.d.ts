import { DefaultSession } from 'next-auth';

declare module 'next-auth' {
  interface Session {
    user: {
      username?: string;
      isAdmin?: boolean;
    } & DefaultSession['user'];
  }

  interface User {
    username?: string;
    isAdmin?: boolean;
  }

  interface Profile {
    preferred_username?: string;
    groups?: string[];
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    refreshToken?: string;
    username?: string;
    isAdmin?: boolean;
  }
}
