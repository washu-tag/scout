import React from 'react';
import { useAuth } from '../auth/GitHubAuthContext.jsx';
import { hasAdminRole } from '../auth/authConfig';

export default function ProtectedSection({ children, requireAdmin = false, fallback = null }) {
  const auth = useAuth();

  // Show loading state
  if (auth.isLoading) {
    return fallback;
  }

  // Show error state
  if (auth.error) {
    return fallback;
  }

  // Must be authenticated
  if (!auth.isAuthenticated) {
    return fallback;
  }

  // If admin is required, check admin role
  if (requireAdmin && !hasAdminRole(auth.user)) {
    return fallback;
  }

  return children;
}