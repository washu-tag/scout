'use client';

import React from 'react';
import { useSession } from 'next-auth/react';

interface ProtectedSectionProps {
  children: React.ReactNode;
  requireAdmin?: boolean;
  fallback?: React.ReactNode;
}

export default function AdminSection({ children, fallback = null }: ProtectedSectionProps) {
  const { data: session, status } = useSession();

  // Dev-only override: render admin content without a real admin session.
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

  // If admin is required, check admin role from session
  if (!session.user?.isAdmin) {
    return fallback;
  }

  return <>{children}</>;
}
