'use client';

import React, { useState, useEffect } from 'react';
import { useSession, signIn, signOut } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FaUser, FaSignOutAlt, FaGithub, FaMoon, FaSun } from 'react-icons/fa';

export default function UserProfile() {
  const { data: session, status } = useSession();
  const router = useRouter();
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
      {status === 'loading' ? (
        <div className="p-2 text-gray-500 dark:text-gray-400" title="Loading...">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-gray-500 dark:border-gray-400"></div>
        </div>
      ) : session ? (
        <button
          onClick={async () => {
            // First sign out from NextAuth
            await signOut({ redirect: false });
            // Then redirect to OAuth2 proxy sign out URL
            if (process.env.NEXT_PUBLIC_OAUTH2_PROXY_SIGN_OUT_URL) {
              window.location.href = process.env.NEXT_PUBLIC_OAUTH2_PROXY_SIGN_OUT_URL;
            } else {
              // Fallback to home page using router
              router.push('/');
            }
          }}
          className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200"
          title={`Signed in as ${session.user?.name || session.user?.email} - Click to sign out`}
        >
          <FaSignOutAlt className="text-xl" />
        </button>
      ) : (
        <button
          onClick={() => signIn('keycloak')}
          className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors duration-200"
          title="Sign in"
        >
          <FaUser className="text-xl" />
        </button>
      )}
    </div>
  );
}
