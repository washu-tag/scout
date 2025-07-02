// GitHub OAuth Configuration
export const githubConfig = {
  clientId: import.meta.env.VITE_GITHUB_CLIENT_ID || 'your-github-client-id',
  clientSecret: import.meta.env.VITE_GITHUB_CLIENT_SECRET, // Should be handled by backend in production
  adminUsers: [
    // Add GitHub usernames of admin users
    'your-github-username',
    // Add more admin usernames as needed
  ],
};

// Helper function to check if user has admin role
export const hasAdminRole = (user) => {
  if (!user?.login) return false;
  return githubConfig.adminUsers.includes(user.login);
};

// Helper function to check if user has specific role
export const hasRole = (user, role) => {
  // For GitHub, we can extend this to check organization membership, teams, etc.
  if (role === 'admin') {
    return hasAdminRole(user);
  }
  // Add more role logic as needed
  return false;
};
