'use client';

import React, { useState, useEffect } from 'react';
import { useSession, signIn } from 'next-auth/react';
import { FaPython } from 'react-icons/fa';
import { SiMinio, SiTemporal, SiGrafana, SiReadthedocs, SiKeycloak } from 'react-icons/si';
import { BiLineChart } from 'react-icons/bi';
import TopBar from '@/components/TopBar';
import AdminSection from '@/components/AdminSection';
import Link from 'next/link';

interface ToolCardProps {
  href: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  color: 'blue' | 'amber' | 'indigo';
  external?: boolean;
}

const ToolCard = ({ href, icon, title, description, color, external = false }: ToolCardProps) => {
  const colorClasses = {
    blue: 'border-blue-200 hover:border-blue-400 bg-blue-50 hover:bg-blue-100',
    amber: 'border-amber-200 hover:border-amber-400 bg-amber-50 hover:bg-amber-100',
    indigo: 'border-indigo-200 hover:border-indigo-400 bg-indigo-50 hover:bg-indigo-100',
  };

  const iconColorClasses = {
    blue: 'bg-blue-500',
    amber: 'bg-amber-500',
    indigo: 'bg-indigo-500',
  };

  return (
    <a
      href={href}
      {...(external && { target: '_blank', rel: 'noopener noreferrer' })}
      className={`group relative p-6 rounded-2xl border-2 transition-all duration-300 hover:-translate-y-2 hover:shadow-2xl no-underline ${colorClasses[color]} dark:bg-gray-800 dark:border-gray-700 dark:hover:border-gray-600 dark:hover:bg-gray-750`}
    >
      <div className="flex items-start gap-6">
        <div
          className={`p-4 rounded-2xl transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3 text-white ${iconColorClasses[color]}`}
        >
          {icon}
        </div>
        <div className="flex-1">
          <div className="flex items-start justify-between mb-2">
            <h3 className="text-2xl font-bold text-gray-900 dark:text-white group-hover:text-gray-800 dark:group-hover:text-gray-100">
              {title}
            </h3>
            {external && (
              <svg
                className="w-5 h-5 text-gray-400 group-hover:text-gray-600 transition-colors"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                />
              </svg>
            )}
          </div>
          <p className="text-gray-600 dark:text-gray-300 group-hover:text-gray-700 dark:group-hover:text-gray-200">
            {description}
          </p>
        </div>
      </div>
    </a>
  );
};

const ToolsGrid = () => {
  const [subdomainUrls, setSubdomainUrls] = useState<Record<string, string>>({});

  useEffect(() => {
    // Generate all subdomain URLs once on client side where window is available
    const protocol = window.location.protocol;
    const hostname = window.location.hostname;

    const getUrl = (subdomain: string, path: string = '') => {
      const normalizedPath = path ? (path.startsWith('/') ? path : `/${path}`) : '';
      return `${protocol}//${subdomain}.${hostname}${normalizedPath}`;
    };

    setSubdomainUrls({
      jupyter: getUrl('jupyter'),
      superset: getUrl('superset'),
      minio: getUrl('minio'),
      temporal: getUrl('temporal', '/auth/sso'),
      grafana: getUrl('grafana'),
      keycloak: getUrl('keycloak', '/admin/scout/console'),
    });

    console.debug('[Scout] Subdomain URLs generated', { protocol, hostname });
  }, []);

  // Don't render until subdomain URLs are set on client side
  if (Object.keys(subdomainUrls).length === 0) {
    return (
      <div className="animate-pulse">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-12">
          <div className="h-32 bg-gray-100 dark:bg-gray-800 rounded-2xl"></div>
          <div className="h-32 bg-gray-100 dark:bg-gray-800 rounded-2xl"></div>
        </div>
        <div className="h-32 bg-gray-100 dark:bg-gray-800 rounded-2xl mb-12"></div>
      </div>
    );
  }

  return (
    <>
      {/* Main Tools Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-12">
        <ToolCard
          href={subdomainUrls.jupyter}
          icon={<FaPython className="text-4xl" />}
          title="Notebooks"
          description="Interactive data analysis and exploration with JupyterHub"
          color="blue"
        />

        <ToolCard
          href={subdomainUrls.superset}
          icon={<BiLineChart className="text-4xl" />}
          title="Analytics"
          description="Visualize data and create dashboards with Apache Superset"
          color="amber"
        />
      </div>

      {/* Documentation */}
      <div className="grid mb-12">
        <ToolCard
          href="https://washu-scout.readthedocs.io/en/latest/"
          icon={<SiReadthedocs className="text-3xl" />}
          title="Documentation"
          description="Learn how to use Scout effectively"
          color="indigo"
          external={true}
        />
      </div>

      {/* Admin Tools Section - Only visible to admins */}
      <AdminSection requireAdmin={true}>
        <div className="mb-12">
          <h3 className="text-sm font-medium text-gray-500 dark:text-gray-400 mb-4 uppercase tracking-wide flex items-center">
            <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
              />
            </svg>
            Admin Tools
          </h3>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <a
              href={subdomainUrls.minio}
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
              href={subdomainUrls.temporal}
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
              href={subdomainUrls.grafana}
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

            <a
              href={subdomainUrls.keycloak}
              className="group flex items-center gap-3 p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-purple-200 dark:hover:border-purple-800 hover:shadow-md transition-all duration-300 no-underline"
            >
              <div className="w-10 h-10 rounded-lg bg-purple-100 dark:bg-purple-900/50 flex items-center justify-center flex-shrink-0 group-hover:scale-110 transition-transform">
                <SiKeycloak className="text-xl text-purple-600 dark:text-purple-400" />
              </div>
              <div>
                <span className="font-medium text-gray-900 dark:text-white">User Management</span>
                <p className="text-xs text-gray-500 dark:text-gray-400">Users & permissions</p>
              </div>
            </a>
          </div>
        </div>
      </AdminSection>
    </>
  );
};

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
    <div className="min-h-screen w-full bg-white dark:bg-gray-900 transition-colors duration-500">
      {/* Header */}
      <div className="w-full px-8 py-6 border-b border-gray-100 dark:border-gray-800">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <Link href={'/'} className="flex items-center gap-3">
            <div>
              <h1 className="text-4xl font-bold text-gray-900 dark:text-white">Scout</h1>
              <p className="text-sm text-gray-500 dark:text-gray-400 pt-2">
                Radiology Report Explorer
              </p>
            </div>
          </Link>
          <TopBar />
        </div>
      </div>

      <div
        className={`max-w-5xl mx-auto px-8 py-12 transition-all duration-1000 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
      >
        {/* Tools Grid */}
        <ToolsGrid />

        {/* Compact Footer */}
        <footer className="text-center py-6 mt-8 border-t border-gray-100 dark:border-gray-800">
          <p className="text-xs text-gray-400 dark:text-gray-500">
            © {new Date().getFullYear()} Translational AI Group, Washington University in St. Louis
          </p>
        </footer>
      </div>
    </div>
  );
}
