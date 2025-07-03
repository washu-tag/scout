'use client';

import React, { useState, useEffect } from 'react';
import { useSession, signIn } from 'next-auth/react';
import { FaPython } from 'react-icons/fa';
import { SiMinio, SiTemporal, SiGrafana, SiReadthedocs } from 'react-icons/si';
import { BiLineChart } from 'react-icons/bi';
import TopBar from '@/components/TopBar';
import AdminSection from '@/components/AdminSection';

// Reusable card styles for DRY code
const cardStyles = {
  base: "group relative overflow-hidden bg-white dark:bg-gray-900 rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 hover:-translate-y-1 border border-gray-200 dark:border-gray-800 no-underline",
  content: "relative p-8 md:p-10",
  flex: "flex items-center gap-6",
  icon: "flex-shrink-0 w-24 h-24 rounded-2xl flex items-center justify-center group-hover:scale-110 transition-transform duration-300",
  title: "text-3xl font-semibold text-gray-900 dark:text-white mb-3",
  description: "text-lg text-gray-600 dark:text-gray-400 leading-relaxed"
};

const getColorClasses = (color: 'blue' | 'amber' | 'indigo') => ({
  gradient: `absolute inset-0 bg-gradient-to-br from-${color}-500/10 to-transparent dark:from-${color}-400/10 opacity-0 group-hover:opacity-100 transition-opacity duration-300`,
  iconBg: `bg-${color}-100 dark:bg-${color}-900/50`,
  iconText: `text-6xl text-${color}-600 dark:text-${color}-400`
});

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
    <div className="min-h-screen w-full bg-gradient-to-b from-gray-50 to-white dark:from-gray-900 dark:to-gray-800 transition-colors duration-500">
      {/* Header */}
      <div className="pt-2 pr-2 mx-auto flex justify-end">
        <TopBar />
      </div>

      <div
        className={`max-w-7xl mx-auto px-6 md:px-8 transition-all duration-1000 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
      >
        {/* Hero Section */}
        <div className="flex flex-col items-center justify-center py-12 md:py-20 text-center">
          <div className="space-y-6">
            <h1 className="text-6xl md:text-8xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-purple-600 dark:from-blue-400 dark:to-purple-400">
              Welcome to Scout
            </h1>
            <p className="text-lg md:text-xl text-gray-600 dark:text-gray-400 max-w-2xl mx-auto leading-relaxed">
              A radiology report exploration platform
            </p>
          </div>
        </div>

        {/* Main Tools Section */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-16">
          <a href="/jupyter" className={cardStyles.base}>
            <div className={getColorClasses('blue').gradient} />
            <div className={cardStyles.content}>
              <div className={cardStyles.flex}>
                <div className={`${cardStyles.icon} ${getColorClasses('blue').iconBg}`}>
                  <FaPython className={getColorClasses('blue').iconText} />
                </div>
                <div className="flex-1">
                  <h2 className={cardStyles.title}>Notebooks</h2>
                  <p className={cardStyles.description}>
                    Interactive data analysis and exploration with JupyterHub
                  </p>
                </div>
              </div>
            </div>
          </a>

          <a href="/" className={cardStyles.base}>
            <div className={getColorClasses('amber').gradient} />
            <div className={cardStyles.content}>
              <div className={cardStyles.flex}>
                <div className={`${cardStyles.icon} ${getColorClasses('amber').iconBg}`}>
                  <BiLineChart className={getColorClasses('amber').iconText} />
                </div>
                <div className="flex-1">
                  <h2 className={cardStyles.title}>Analytics</h2>
                  <p className={cardStyles.description}>
                    Visualize data and create dashboards with Apache Superset
                  </p>
                </div>
              </div>
            </div>
          </a>
        </div>

        {/* Documentation Section */}
        <div className="mb-16">
          <a
            href="https://washu-scout.readthedocs.io/en/latest/"
            target="_blank"
            rel="noopener noreferrer"
            className="group relative overflow-hidden bg-white dark:bg-gray-900 rounded-2xl shadow-sm hover:shadow-lg transition-all duration-300 border border-gray-200 dark:border-gray-800 no-underline block"
          >
            <div className="absolute inset-0 bg-gradient-to-r from-indigo-500/5 to-purple-500/5 dark:from-indigo-400/5 dark:to-purple-400/5 opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
            <div className="relative p-6 md:p-8">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-5">
                  <div className="w-16 h-16 rounded-xl bg-indigo-100 dark:bg-indigo-900/50 flex items-center justify-center group-hover:scale-110 transition-transform duration-300">
                    <SiReadthedocs className="text-3xl text-indigo-600 dark:text-indigo-400" />
                  </div>
                  <div>
                    <h2 className="text-2xl font-semibold text-gray-900 dark:text-white">
                      Documentation
                    </h2>
                    <p className="text-lg text-gray-600 dark:text-gray-400">
                      Learn how to use Scout effectively
                    </p>
                  </div>
                </div>
                <svg className="w-6 h-6 text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-300 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </div>
            </div>
          </a>
        </div>

        {/* Admin Tools Section - Only visible to admins */}
        <AdminSection requireAdmin={true}>
          <div className="bg-gray-50 dark:bg-gray-900/50 rounded-2xl border border-gray-200 dark:border-gray-800 p-8 mb-16">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-6 flex items-center">
              <svg className="w-5 h-5 mr-2 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              Admin Tools
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <a
                href="/minio/"
                className="group flex items-center gap-3 p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-red-200 dark:hover:border-red-800 hover:shadow-md transition-all duration-300 no-underline"
              >
                <div className="w-10 h-10 rounded-lg bg-red-100 dark:bg-red-900/50 flex items-center justify-center flex-shrink-0 group-hover:scale-110 transition-transform">
                  <SiMinio className="text-xl text-red-600 dark:text-red-400" />
                </div>
                <div>
                  <span className="font-medium text-gray-900 dark:text-white">Lake</span>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Object storage</p>
                </div>
              </a>

              <a
                href="/temporal"
                className="group flex items-center gap-3 p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-emerald-200 dark:hover:border-emerald-800 hover:shadow-md transition-all duration-300 no-underline"
              >
                <div className="w-10 h-10 rounded-lg bg-emerald-100 dark:bg-emerald-900/50 flex items-center justify-center flex-shrink-0 group-hover:scale-110 transition-transform">
                  <SiTemporal className="text-xl text-emerald-600 dark:text-emerald-400" />
                </div>
                <div>
                  <span className="font-medium text-gray-900 dark:text-white">Orchestrator</span>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Workflows</p>
                </div>
              </a>

              <a
                href="/grafana"
                className="group flex items-center gap-3 p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-orange-200 dark:hover:border-orange-800 hover:shadow-md transition-all duration-300 no-underline"
              >
                <div className="w-10 h-10 rounded-lg bg-orange-100 dark:bg-orange-900/50 flex items-center justify-center flex-shrink-0 group-hover:scale-110 transition-transform">
                  <SiGrafana className="text-xl text-orange-600 dark:text-orange-400" />
                </div>
                <div>
                  <span className="font-medium text-gray-900 dark:text-white">Monitor</span>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Metrics & logs</p>
                </div>
              </a>
            </div>
          </div>
        </AdminSection>

        {/* Footer */}
        <footer className="text-center py-8 mt-16 border-t border-gray-200 dark:border-gray-800">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            © {new Date().getFullYear()} Translational AI Group, Washington University in St. Louis
          </p>
        </footer>
      </div>
    </div>
  );
}
