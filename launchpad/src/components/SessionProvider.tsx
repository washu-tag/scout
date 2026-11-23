'use client';

import { SessionProvider } from 'next-auth/react';

export default function AuthSessionProvider({ children }: { children: React.ReactNode }) {
  // No refetch on focus: one that fails (a laptop waking before its network) would clear the
  // session and send the window to sign-in, unloading every app. The shell checks the session
  // itself (/api/session).
  return (
    <SessionProvider basePath="/api/auth" refetchOnWindowFocus={false}>
      {children}
    </SessionProvider>
  );
}
