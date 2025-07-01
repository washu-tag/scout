// OIDC Configuration for Keycloak integration
export const authConfig = {
  authority: `${window.location.origin}/auth/realms/scout`,
  client_id: 'launchpad',
  redirect_uri: `${window.location.origin}`,
  post_logout_redirect_uri: `${window.location.origin}`,
  response_type: 'code',
  scope: 'openid profile email',
  automaticSilentRenew: true,
  includeIdTokenInSilentRenew: true,
  loadUserInfo: true,
  onSigninCallback: () => {
    // Remove the query string after successful authentication
    window.history.replaceState({}, document.title, window.location.pathname);
  }
};

// Helper function to check if user has admin role
export const hasAdminRole = (user) => {
  if (!user?.profile?.groups) return false;
  return user.profile.groups.includes('scout-admin');
};

// Helper function to check if user has specific role
export const hasRole = (user, role) => {
  if (!user?.profile?.groups) return false;
  return user.profile.groups.includes(role);
};
