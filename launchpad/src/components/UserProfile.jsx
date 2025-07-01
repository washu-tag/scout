import React, { useState, useEffect } from 'react';
import { useAuth } from 'react-oidc-context';
import { FaUser, FaSignOutAlt, FaGithub, FaMoon, FaSun } from 'react-icons/fa';

export default function UserProfile() {
  const auth = useAuth();
  const [isDark, setIsDark] = useState(false);

  // Initialize theme from localStorage or system preference
  useEffect(() => {
    const savedTheme = localStorage.getItem('theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const shouldBeDark = savedTheme === 'dark' || (!savedTheme && systemPrefersDark);
    
    setIsDark(shouldBeDark);
    applyTheme(shouldBeDark);
  }, []);

  const applyTheme = (dark) => {
    const root = document.documentElement;
    
    if (dark) {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  };

  const toggleTheme = () => {
    const newTheme = !isDark;
    setIsDark(newTheme);
    localStorage.setItem('theme', newTheme ? 'dark' : 'light');
    applyTheme(newTheme);
  };

  const getDisplayName = () => {
    if (!auth.user?.profile) return null;
    return auth.user.profile.name || 
           auth.user.profile.preferred_username || 
           auth.user.profile.email || 
           'User';
  };

  return (
    <div className="flex items-center gap-2">
      {/* GitHub Link */}
      <a
        href="https://github.com/washu-tag/scout"
        target="_blank"
        rel="noopener noreferrer"
        className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200"
        title="Scout on GitHub"
      >
        <FaGithub className="text-xl" />
      </a>

      {/* Theme Toggle */}
      <button
        onClick={toggleTheme}
        className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200"
        title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        {isDark ? <FaSun className="text-xl" /> : <FaMoon className="text-xl" />}
      </button>

      {/* Authentication Status */}
      {auth.isLoading ? (
        <div className="p-2 text-gray-500 dark:text-gray-400" title="Loading...">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-gray-500 dark:border-gray-400"></div>
        </div>
      ) : auth.error ? (
        <div className="p-2 text-red-500" title="Authentication Error">
          <FaUser className="text-xl" />
        </div>
      ) : auth.isAuthenticated ? (
        <button
          onClick={() => auth.signoutRedirect()}
          className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200"
          title={`Signed in as ${getDisplayName()} - Click to sign out`}
        >
          <FaSignOutAlt className="text-xl" />
        </button>
      ) : (
        <button
          onClick={() => auth.signinRedirect()}
          className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200"
          title="Sign in to Scout"
        >
          <FaUser className="text-xl" />
        </button>
      )}
    </div>
  );
}