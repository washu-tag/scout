'use client';

import React, { useState, useEffect } from 'react';
import { HiExclamationCircle, HiArrowRight } from 'react-icons/hi';

export default function AuthError() {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:bg-gradient-to-br dark:from-gray-900 dark:via-gray-900 dark:to-gray-800 transition-colors duration-500 flex items-center justify-center py-12">
      <div
        className={`w-full max-w-md px-6 transition-all duration-700 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
      >
        <div className="bg-white/60 backdrop-blur-sm border-2 border-gray-200/50 dark:bg-gray-800/60 dark:border-gray-700/50 rounded-3xl p-8 shadow-lg text-center">
          <div className="flex justify-center mb-6">
            <div className="w-16 h-16 rounded-2xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
              <HiExclamationCircle className="text-4xl text-red-500 dark:text-red-400" />
            </div>
          </div>

          <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-3">
            Authentication Error
          </h1>

          <p className="text-gray-600 dark:text-gray-300 mb-8 leading-relaxed">
            We were unable to sign you in. This may be a temporary issue. Please try again.
          </p>

          <a
            href="/"
            className="group inline-flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-blue-500 to-indigo-600 hover:from-blue-600 hover:to-indigo-700 text-white font-semibold rounded-xl shadow-lg shadow-blue-500/25 hover:shadow-xl hover:shadow-blue-500/30 transition-all duration-300 no-underline"
          >
            <span>Try Again</span>
            <HiArrowRight className="transform group-hover:translate-x-1 transition-transform duration-300" />
          </a>
        </div>
      </div>
    </div>
  );
}
