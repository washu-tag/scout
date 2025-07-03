'use client';

import React, { useState, useEffect } from 'react';
import { useSession, signIn } from 'next-auth/react';
import { FaPython } from 'react-icons/fa';
import { SiMinio, SiTemporal, SiGrafana, SiReadthedocs } from 'react-icons/si';
import { BiLineChart } from 'react-icons/bi';
import UserProfile from '@/components/UserProfile';
import AdminSection from '@/components/AdminSection';

export default function Home() {
  const [mounted, setMounted] = useState(false);
  const { data: session, status } = useSession();

  useEffect(() => {
    setMounted(true);
  }, []);

  // Auto-login: redirect to sign in if not authenticated
  useEffect(() => {
    if (status !== 'loading' && !session) {
      signIn('keycloak');
    }
  }, [status, session]);

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-blue-50 via-white to-purple-50 dark:from-gray-900 dark:via-gray-800 dark:to-gray-900 p-6 md:p-8 transition-colors duration-500">
      {/* Header with Logo and Icons */}
      <div className="flex justify-end items-center">
        <UserProfile />
      </div>
      <div
        className={`max-w-6xl mx-auto transition-all duration-1000 ${mounted ? 'opacity-100' : 'opacity-0'}`}
      >
        {/* Hero Section */}
        <div className="flex flex-col items-center justify-center py-16 md:py-24 text-center">
          <h1 className="text-6xl md:text-8xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-purple-600 dark:from-blue-400 dark:to-purple-400 mb-6 tracking-tight">
            Welcome to Scout
          </h1>
          <p className="text-lg md:text-xl text-gray-600 dark:text-gray-300 max-w-xl">
            A radiology report exploration tool brought to you by the Translational AI Group (TAG)
            at WashU
          </p>
        </div>

        {/* Main Tools Section */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-8 mb-12">
          <a
            href="/jupyter"
            className="group flex items-center justify-center p-8 md:p-10 bg-white bg-opacity-70 dark:bg-gray-800 dark:bg-opacity-70 backdrop-blur-sm rounded-2xl shadow-lg hover:shadow-xl transition-all duration-300 hover:-translate-y-1 border border-blue-100 dark:border-gray-700 no-underline"
          >
            <div className="flex flex-col md:flex-row items-center text-center md:text-left gap-6">
              <div className="p-5 rounded-full bg-blue-50 dark:bg-blue-900 text-blue-600 dark:text-blue-400 group-hover:bg-blue-100 dark:group-hover:bg-blue-800 transition-colors duration-300">
                <FaPython className="text-6xl md:text-7xl" />
              </div>
              <div>
                <h2 className="text-3xl md:text-4xl font-semibold text-gray-800 dark:text-gray-100 mb-2">
                  Notebooks
                </h2>
                <p className="text-gray-600 dark:text-gray-300">
                  Launch interactive notebooks for data analysis with JupyterHub
                </p>
              </div>
            </div>
          </a>

          {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
          <a
            href="/"
            className="group flex items-center justify-center p-8 md:p-10 bg-white bg-opacity-70 dark:bg-gray-800 dark:bg-opacity-70 backdrop-blur-sm rounded-2xl shadow-lg hover:shadow-xl transition-all duration-300 hover:-translate-y-1 border border-yellow-100 dark:border-gray-700 no-underline"
          >
            <div className="flex flex-col md:flex-row items-center text-center md:text-left gap-6">
              <div className="p-5 rounded-full bg-yellow-50 dark:bg-yellow-900 text-yellow-600 dark:text-yellow-400 group-hover:bg-yellow-100 dark:group-hover:bg-yellow-800 transition-colors duration-300">
                <BiLineChart className="text-6xl md:text-7xl" />
              </div>
              <div>
                <h2 className="text-3xl md:text-4xl font-semibold text-gray-800 dark:text-gray-100 mb-2">
                  Analytics
                </h2>
                <p className="text-gray-600 dark:text-gray-300">
                  Explore data visualizations and dashboards with Superset
                </p>
              </div>
            </div>
          </a>
        </div>

        {/* Documentation Section */}
        <div className="mb-12">
          <a
            href="https://washu-scout.readthedocs.io/en/latest/"
            target="_blank"
            rel="noopener noreferrer"
            className="group w-full flex items-center justify-center p-6 md:p-8 bg-white bg-opacity-70 dark:bg-gray-800 dark:bg-opacity-70 backdrop-blur-sm rounded-xl shadow-md hover:shadow-lg transition-all duration-300 hover:-translate-y-1 border border-purple-100 dark:border-gray-700 no-underline"
          >
            <div className="flex items-center gap-4">
              <div className="p-4 rounded-full bg-purple-50 dark:bg-purple-900 text-purple-600 dark:text-purple-400 group-hover:bg-purple-100 dark:group-hover:bg-purple-800 transition-colors duration-300">
                <SiReadthedocs className="text-3xl" />
              </div>
              <div className="text-left">
                <h2 className="text-2xl font-medium text-gray-800 dark:text-gray-100">
                  Documentation
                </h2>
                <p className="text-gray-600 dark:text-gray-300">
                  Learn how to use Scout effectively
                </p>
              </div>
            </div>
          </a>
        </div>

        {/* Admin Tools Section - Only visible to admins */}
        <AdminSection requireAdmin={true}>
          <div className="bg-white bg-opacity-80 dark:bg-gray-800 dark:bg-opacity-80 backdrop-blur-sm rounded-xl shadow-lg p-6 mb-12">
            <h2 className="text-xl font-semibold text-gray-700 dark:text-gray-200 mb-6 flex items-center">
              <span className="inline-block w-1 h-6 bg-gray-700 dark:bg-gray-300 mr-3 rounded"></span>
              Admin Tools
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <a
                href="/minio/"
                className="flex items-center gap-3 p-4 rounded-lg bg-gradient-to-br from-red-50 to-white dark:from-red-900/20 dark:to-gray-800 text-red-700 dark:text-red-400 border border-red-100 dark:border-red-800 shadow hover:shadow-md transition-all duration-300 hover:-translate-y-1 no-underline"
              >
                <SiMinio className="text-2xl" />
                <span className="font-medium">Lake</span>
              </a>

              <a
                href="/temporal"
                className="flex items-center gap-3 p-4 rounded-lg bg-gradient-to-br from-emerald-50 to-white dark:from-emerald-900/20 dark:to-gray-800 text-emerald-700 dark:text-emerald-400 border border-emerald-100 dark:border-emerald-800 shadow hover:shadow-md transition-all duration-300 hover:-translate-y-1 no-underline"
              >
                <SiTemporal className="text-2xl" />
                <span className="font-medium">Orchestrator</span>
              </a>

              <a
                href="/grafana"
                className="flex items-center gap-3 p-4 rounded-lg bg-gradient-to-br from-orange-50 to-white dark:from-orange-900/20 dark:to-gray-800 text-orange-700 dark:text-orange-400 border border-orange-100 dark:border-orange-800 shadow hover:shadow-md transition-all duration-300 hover:-translate-y-1 no-underline"
              >
                <SiGrafana className="text-2xl" />
                <span className="font-medium">Monitor</span>
              </a>
            </div>
          </div>
        </AdminSection>

        {/* Footer */}
        <div className="text-center text-gray-500 dark:text-gray-400 text-sm">
          © {new Date().getFullYear()} Translational AI Group, Washington University in St. Louis
        </div>
      </div>
    </div>
  );
}
