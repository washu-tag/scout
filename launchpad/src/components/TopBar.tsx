'use client';

import React, { useState, useEffect } from 'react';
import { useSession, signIn } from 'next-auth/react';
import { FaUser, FaGithub, FaMoon, FaSun } from 'react-icons/fa';
import UserDropdown from './UserDropdown';

export default function TopBar() {
  const { data: session, status } = useSession();
  const [isDark, setIsDark] = useState(false);

  // Initialize theme from localStorage or system preference
  useEffect(() => {
    const savedTheme = localStorage.getItem('theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const shouldBeDark = savedTheme === 'dark' || (!savedTheme && systemPrefersDark);

    setIsDark(shouldBeDark);
    applyTheme(shouldBeDark);
  }, []);

  const applyTheme = (dark: boolean) => {
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

  return (
    <div className="flex items-center gap-2">
      {/* GitHub Link */}
      <a
        href="https://github.com/washu-tag/scout"
        target="_blank"
        rel="noopener noreferrer"
        className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200 cursor-pointer"
        title="Scout on GitHub"
      >
        <FaGithub className="text-xl" />
      </a>

      {/* Theme Toggle */}
      <button
        onClick={toggleTheme}
        className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200 cursor-pointer"
        title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        {isDark ? <FaSun className="text-xl" /> : <FaMoon className="text-xl" />}
      </button>

      {/* Authentication Status */}
      {status === 'loading' ? (
        <div className="p-2 text-gray-500 dark:text-gray-400" title="Loading...">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-gray-500 dark:border-gray-400"></div>
        </div>
      ) : session ? (
        <UserDropdown />
      ) : (
        <button
          onClick={() => signIn('keycloak')}
          className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200 cursor-pointer"
          title="Sign in"
        >
          <FaUser className="text-xl" />
        </button>
      )}
    </div>
  );
}
