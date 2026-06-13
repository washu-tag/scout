'use client';

import React from 'react';
import { useSession } from 'next-auth/react';
import { canManageUsers } from '@/lib/roles';

interface ProtectedSectionProps {
  children: React.ReactNode;
  /** Role required to see the children: full admin (default) or the delegated manage-users capability (which admins also hold). */
  capability?: 'admin' | 'manage-users';
  fallback?: React.ReactNode;
}

export default function AdminSection({
  children,
  capability = 'admin',
  fallback = null,
}: ProtectedSectionProps) {
  const { data: session, status } = useSession();

  // Dev-only override: render gated content without a real session.
  const devAdmin =
    process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_DEV_ADMIN === 'true';
  if (devAdmin) {
    return <>{children}</>;
  }

  // Show loading state - render children with opacity 0 to maintain layout
  if (status === 'loading') {
    return <div className="opacity-0 pointer-events-none">{children}</div>;
  }

  // Must be authenticated
  if (!session) {
    return fallback;
  }

  const allowed =
    capability === 'admin' ? !!session.user?.isAdmin : canManageUsers(session.user?.roles);
  if (!allowed) {
    return fallback;
  }

  return <>{children}</>;
}
