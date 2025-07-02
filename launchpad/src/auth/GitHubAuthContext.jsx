import React, { createContext, useContext, useState, useEffect } from 'react';
import Cookies from 'js-cookie';

const GitHubAuthContext = createContext();

export const useAuth = () => {
  const context = useContext(GitHubAuthContext);
  if (!context) {
    throw new Error('useAuth must be used within a GitHubAuthProvider');
  }
  return context;
};

export const GitHubAuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  const clientId = import.meta.env.VITE_GITHUB_CLIENT_ID || 'your-github-client-id';
  const clientSecret = import.meta.env.VITE_GITHUB_CLIENT_SECRET; // Only for development, should be handled by backend
  
  useEffect(() => {
    // Check for existing user session
    const savedUser = Cookies.get('github_user');
    if (savedUser) {
      try {
        const userData = JSON.parse(savedUser);
        setUser(userData);
        setIsAuthenticated(true);
      } catch (err) {
        console.error('Error parsing saved user:', err);
        Cookies.remove('github_user');
      }
    }

    // Check if we're returning from GitHub OAuth
    const urlParams = new URLSearchParams(window.location.search);
    const code = urlParams.get('code');
    const state = urlParams.get('state');

    if (code && state === 'github_oauth') {
      exchangeCodeForToken(code);
      // Clean up URL
      const url = new URL(window.location);
      url.searchParams.delete('code');
      url.searchParams.delete('state');
      window.history.replaceState({}, '', url);
    } else {
      setIsLoading(false);
    }
  }, []);

  const exchangeCodeForToken = async (code) => {
    try {
      setIsLoading(true);
      setError(null);

      // Exchange code for access token
      const tokenResponse = await fetch('https://github.com/login/oauth/access_token', {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          client_id: clientId,
          client_secret: clientSecret, // In production, this should be done by your backend
          code: code,
        }),
      });

      if (!tokenResponse.ok) {
        throw new Error('Failed to exchange code for token');
      }

      const tokenData = await tokenResponse.json();
      
      if (tokenData.error) {
        throw new Error(tokenData.error_description || tokenData.error);
      }

      // Fetch user information
      const userResponse = await fetch('https://api.github.com/user', {
        headers: {
          Authorization: `token ${tokenData.access_token}`,
          Accept: 'application/vnd.github.v3+json',
        },
      });

      if (!userResponse.ok) {
        throw new Error('Failed to fetch user information');
      }

      const userData = await userResponse.json();

      // Fetch user's email (if not public)
      const emailResponse = await fetch('https://api.github.com/user/emails', {
        headers: {
          Authorization: `token ${tokenData.access_token}`,
          Accept: 'application/vnd.github.v3+json',
        },
      });

      let email = userData.email;
      if (!email && emailResponse.ok) {
        const emails = await emailResponse.json();
        const primaryEmail = emails.find(e => e.primary);
        email = primaryEmail ? primaryEmail.email : emails[0]?.email;
      }

      const enrichedUser = {
        ...userData,
        email,
        access_token: tokenData.access_token,
        profile: {
          name: userData.name || userData.login,
          preferred_username: userData.login,
          email: email,
          avatar_url: userData.avatar_url,
        }
      };

      setUser(enrichedUser);
      setIsAuthenticated(true);
      
      // Save user data to cookie (excluding sensitive token for security)
      const userForStorage = { ...enrichedUser };
      delete userForStorage.access_token;
      Cookies.set('github_user', JSON.stringify(userForStorage), { expires: 7 });

    } catch (err) {
      console.error('GitHub OAuth error:', err);
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  const signinRedirect = () => {
    const params = new URLSearchParams({
      client_id: clientId,
      redirect_uri: window.location.origin,
      scope: 'user:email read:user',
      state: 'github_oauth', // Simple state for validation
    });

    window.location.href = `https://github.com/login/oauth/authorize?${params}`;
  };

  const signoutRedirect = () => {
    setUser(null);
    setIsAuthenticated(false);
    setError(null);
    Cookies.remove('github_user');
    
    // GitHub doesn't have a centralized logout, so we just clear local state
    // Optionally redirect to home or show a message
  };

  const value = {
    user,
    isLoading,
    error,
    isAuthenticated,
    signinRedirect,
    signoutRedirect,
  };

  return (
    <GitHubAuthContext.Provider value={value}>
      {children}
    </GitHubAuthContext.Provider>
  );
};