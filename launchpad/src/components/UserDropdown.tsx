'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useSession, signOut } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FaSignOutAlt, FaChevronDown } from 'react-icons/fa';

export default function UserDropdown() {
  const { data: session } = useSession();
  const router = useRouter();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Handle click outside to close dropdown
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!session) return null;

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsDropdownOpen(!isDropdownOpen)}
        className="flex items-center gap-2 p-2 text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-gray-100 transition-colors duration-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer"
        title="User menu"
      >
        <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-gray-700 dark:text-gray-300 font-semibold text-sm">
          {session.user?.name?.charAt(0).toUpperCase() ||
            session.user?.email?.charAt(0).toUpperCase() ||
            'U'}
        </div>
        <FaChevronDown
          className={`text-xs transition-transform duration-200 ${isDropdownOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {/* Dropdown Menu */}
      {isDropdownOpen && (
        <div className="absolute right-0 mt-2 w-64 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 py-2 z-50">
          <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {session.user?.name || 'User'}
            </p>
            <p className="text-sm text-gray-500 dark:text-gray-400 truncate">
              {session.user?.email || 'No email'}
            </p>
          </div>

          <button
            onClick={async () => {
              setIsDropdownOpen(false);
              // First sign out from NextAuth
              await signOut({ redirect: false });

              // Get the OAuth2 sign out URL from the API
              try {
                const response = await fetch('/launchpad/api/auth/signout', {
                  method: 'POST',
                });
                const data = await response.json();

                if (data.redirectUrl) {
                  window.location.href = data.redirectUrl;
                } else {
                  // Fallback to home page using router
                  router.push('/');
                }
              } catch {
                // If API call fails, fallback to home page
                router.push('/');
              }
            }}
            className="w-full px-4 py-2 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors duration-200 flex items-center gap-3 cursor-pointer"
          >
            <FaSignOutAlt className="text-base" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
