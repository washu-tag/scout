import { DefaultSession } from 'next-auth';

declare module 'next-auth' {
  interface Session {
    accessToken?: string;
    user: {
      username?: string;
      isAdmin?: boolean;
      groups?: string[];
    } & DefaultSession['user'];
  }

  interface User {
    username?: string;
    isAdmin?: boolean;
    groups?: string[];
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    username?: string;
    groups?: string[];
  }
}
