'use client';

import React, { useState, useEffect } from 'react';
import { useSession, signIn } from 'next-auth/react';
import { FaPython } from 'react-icons/fa';
import { SiGrafana, SiKeycloak, SiMinio, SiReadthedocs, SiTemporal } from 'react-icons/si';
import { BiLineChart, BiMessageSquareDetail } from 'react-icons/bi';
import {
  HiArrowRight,
  HiCube,
  HiCog,
  HiOutlineBookOpen,
  HiUserGroup,
  HiChartBar,
  HiSparkles,
  HiClipboardCheck,
} from 'react-icons/hi';
import TopBar from '@/components/TopBar';
import AdminSection from '@/components/AdminSection';

interface ServiceCardProps {
  href: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  color: 'blue' | 'amber' | 'green';
  external?: boolean;
}

const ServiceCard = ({
  href,
  icon,
  title,
  description,
  color,
  external = false,
}: ServiceCardProps) => {
  const colorClasses = {
    blue: 'bg-gradient-to-br from-blue-50 to-blue-100/50 border-2 border-blue-200/50 hover:border-blue-400 hover:shadow-xl hover:shadow-blue-500/20 hover:from-blue-100 hover:to-blue-200/50 dark:from-blue-900/20 dark:to-blue-800/10 dark:border-blue-800/50 dark:hover:border-blue-500 dark:hover:shadow-2xl dark:hover:shadow-blue-500/40 dark:hover:from-blue-800/30 dark:hover:to-blue-700/20',
    amber:
      'bg-gradient-to-br from-amber-50 to-yellow-100/50 border-2 border-amber-200/50 hover:border-amber-400 hover:shadow-xl hover:shadow-amber-500/20 hover:from-amber-100 hover:to-amber-200/50 dark:from-amber-900/20 dark:to-amber-800/10 dark:border-amber-800/50 dark:hover:border-amber-500 dark:hover:shadow-2xl dark:hover:shadow-amber-500/40 dark:hover:from-amber-800/30 dark:hover:to-amber-700/20',
    green:
      'bg-gradient-to-br from-emerald-50 to-green-100/50 border-2 border-green-200/50 hover:border-green-400 hover:shadow-xl hover:shadow-green-500/20 hover:from-emerald-100 hover:to-green-200/50 dark:from-emerald-900/20 dark:to-emerald-800/10 dark:border-emerald-800/50 dark:hover:border-emerald-500 dark:hover:shadow-2xl dark:hover:shadow-emerald-500/40 dark:hover:from-emerald-800/30 dark:hover:to-emerald-700/20',
  };

  const iconColorClasses = {
    blue: 'text-blue-600 dark:text-blue-400',
    amber: 'text-amber-600 dark:text-amber-400',
    green: 'text-emerald-600 dark:text-emerald-400',
  };

  const arrowColorClasses = {
    blue: 'text-blue-600 dark:text-blue-400',
    amber: 'text-amber-600 dark:text-amber-400',
    green: 'text-emerald-600 dark:text-emerald-400',
  };

  return (
    <a
      href={href}
      {...(external && { target: '_blank' })}
      className={`group block p-6 ${colorClasses[color]} rounded-2xl transition-all duration-300 no-underline hover:scale-[1.02] hover:-translate-y-1`}
    >
      <div className="flex items-center gap-3 mb-4">
        <div className="w-12 h-12 rounded-xl bg-white dark:bg-gray-800 shadow-md flex items-center justify-center group-hover:scale-125 transition-all duration-300">
          <div className={`text-2xl ${iconColorClasses[color]}`}>{icon}</div>
        </div>
        <h3 className="text-xl font-bold text-gray-900 dark:text-white group-hover:translate-x-1 transition-transform duration-300">
          {title}
        </h3>
      </div>
      <p className="text-sm text-gray-600 dark:text-gray-300 mb-4 leading-relaxed">{description}</p>
      <div
        className={`flex items-center gap-1 ${arrowColorClasses[color]} font-bold text-sm transition-all duration-300`}
      >
        <span className="group-hover:translate-x-1 transition-transform duration-300">Launch</span>
        <HiArrowRight className="transform group-hover:translate-x-3 transition-all duration-300 ease-out" />
      </div>
    </a>
  );
};

interface ToolsGridProps {
  subdomainUrls: Record<string, string>;
  enableChat: boolean;
}

const ToolsGrid = ({ subdomainUrls, enableChat }: ToolsGridProps) => {
  return (
    <div className={`grid grid-cols-1 gap-8 ${enableChat ? 'lg:grid-cols-3' : 'lg:grid-cols-2'}`}>
      <ServiceCard
        href={subdomainUrls.superset}
        icon={<BiLineChart />}
        title="Analytics"
        description="Visual dashboards, business intelligence, and SQL queries"
        color="amber"
        external={true}
      />

      {enableChat && (
        <ServiceCard
          href={subdomainUrls.chat}
          icon={<BiMessageSquareDetail />}
          title="Chat"
          description="AI-powered assistance for Q & A style data queries"
          color="green"
          external={true}
        />
      )}

      <ServiceCard
        href={subdomainUrls.jupyter}
        icon={<FaPython />}
        title="Notebooks"
        description="Interactive data analysis and coding with Jupyter"
        color="blue"
        external={true}
      />
    </div>
  );
};

// Playbook definitions with icons and colors
const PLAYBOOKS = [
  {
    id: 'cohort',
    title: 'Research Cohorting',
    description: 'Build and manage patient cohorts for research studies',
    notebook: 'Cohort.ipynb',
    icon: HiUserGroup,
    colors: {
      gradient: 'from-violet-50 to-purple-50 dark:from-violet-900/20 dark:to-purple-900/10',
      border: 'border-violet-200/50 dark:border-violet-800/50',
      hoverBorder: 'hover:border-violet-400 dark:hover:border-violet-500',
      shadow: 'hover:shadow-violet-200/50 dark:hover:shadow-violet-500/30',
      icon: 'text-violet-600 dark:text-violet-400',
      arrow: 'group-hover:text-violet-600 dark:group-hover:text-violet-400',
    },
  },
  {
    id: 'rads',
    title: 'RADS Dashboard',
    description: 'Explore LI-RADS and BI-RADS reporting trends over time',
    notebook: 'RADS.ipynb',
    icon: HiChartBar,
    colors: {
      gradient: 'from-rose-50 to-pink-50 dark:from-rose-900/20 dark:to-pink-900/10',
      border: 'border-rose-200/50 dark:border-rose-800/50',
      hoverBorder: 'hover:border-rose-400 dark:hover:border-rose-500',
      shadow: 'hover:shadow-rose-200/50 dark:hover:shadow-rose-500/30',
      icon: 'text-rose-600 dark:text-rose-400',
      arrow: 'group-hover:text-rose-600 dark:group-hover:text-rose-400',
    },
  },
  {
    id: 'followup_detection',
    title: 'Clinical Follow-up Monitoring',
    description: 'Review algorithmically-detected follow-up recommendations',
    notebook: 'FollowUpDetection.ipynb',
    icon: HiSparkles,
    colors: {
      gradient: 'from-cyan-50 to-sky-50 dark:from-cyan-900/20 dark:to-sky-900/10',
      border: 'border-cyan-200/50 dark:border-cyan-800/50',
      hoverBorder: 'hover:border-cyan-400 dark:hover:border-cyan-500',
      shadow: 'hover:shadow-cyan-200/50 dark:hover:shadow-cyan-500/30',
      icon: 'text-cyan-600 dark:text-cyan-400',
      arrow: 'group-hover:text-cyan-600 dark:group-hover:text-cyan-400',
    },
  },
  {
    id: 'quality_metrics',
    title: 'Quality Metrics',
    description: 'Analyze report quality and compliance metrics',
    notebook: 'QualityMetrics.ipynb',
    icon: HiClipboardCheck,
    colors: {
      gradient: 'from-emerald-50 to-teal-50 dark:from-emerald-900/20 dark:to-teal-900/10',
      border: 'border-emerald-200/50 dark:border-emerald-800/50',
      hoverBorder: 'hover:border-emerald-400 dark:hover:border-emerald-500',
      shadow: 'hover:shadow-emerald-200/50 dark:hover:shadow-emerald-500/30',
      icon: 'text-emerald-600 dark:text-emerald-400',
      arrow: 'group-hover:text-emerald-600 dark:group-hover:text-emerald-400',
    },
  },
];

interface PlaybooksGridProps {
  playbooksUrl: string;
}

const PlaybooksGrid = ({ playbooksUrl }: PlaybooksGridProps) => {
  return (
    <div className="space-y-3">
      {PLAYBOOKS.map((playbook) => {
        const IconComponent = playbook.icon;
        return (
          <a
            key={playbook.id}
            href={`${playbooksUrl}/voila/render/${playbook.id}/${playbook.notebook}`}
            target="_blank"
            rel="noopener noreferrer"
            className={`group relative flex items-center gap-3 p-4 bg-gradient-to-r ${playbook.colors.gradient} border ${playbook.colors.border} ${playbook.colors.hoverBorder} rounded-xl hover:shadow-xl ${playbook.colors.shadow} hover:-translate-y-1 transition-all duration-300 no-underline overflow-hidden`}
          >
            <div className="absolute inset-0 bg-gradient-to-r from-transparent to-transparent group-hover:from-white/5 group-hover:to-white/10 dark:group-hover:from-white/5 dark:group-hover:to-white/10 transition-all duration-300"></div>
            <div className="relative w-10 h-10 rounded-lg bg-white dark:bg-gray-800 flex items-center justify-center flex-shrink-0 shadow-md group-hover:scale-110 transition-transform duration-300">
              <IconComponent className={`text-xl ${playbook.colors.icon}`} />
            </div>
            <div className="relative flex-1 min-w-0">
              <h3 className="text-base font-bold text-gray-900 dark:text-white">
                {playbook.title}
              </h3>
              <p className="text-xs text-gray-600 dark:text-gray-400 leading-snug">
                {playbook.description}
              </p>
            </div>
            <HiArrowRight
              className={`relative text-xl text-gray-300 dark:text-gray-600 ${playbook.colors.arrow} group-hover:translate-x-1 transition-all duration-300 flex-shrink-0`}
            />
          </a>
        );
      })}
    </div>
  );
};

interface ContentGridProps {
  enableChat: boolean;
  enablePlaybooks: boolean;
}

const ContentGrid = ({ enableChat, enablePlaybooks }: ContentGridProps) => {
  const [subdomainUrls, setSubdomainUrls] = useState<Record<string, string>>({});

  useEffect(() => {
    // Generate all subdomain URLs once on client side where window is available
    const protocol = window.location.protocol;
    const host = window.location.host;

    const getUrl = (subdomain: string, path: string = '') => {
      const normalizedPath = path ? (path.startsWith('/') ? path : `/${path}`) : '';
      return `${protocol}//${subdomain}.${host}${normalizedPath}`;
    };

    setSubdomainUrls({
      jupyter: getUrl('jupyter'),
      superset: getUrl('superset'),
      // Cannot redirect without a js modification, see https://github.com/open-webui/open-webui/discussions/7337
      chat: getUrl('chat', '/oauth/oidc/login'),
      playbooks: getUrl('playbooks'),
      minio: getUrl('minio'),
      temporal: getUrl('temporal', '/auth/sso'),
      grafana: getUrl('grafana'),
      keycloak: getUrl('keycloak', '/admin/scout/console'),
    });

    console.debug('[Scout] Subdomain URLs generated', { protocol, host });
    console.debug('[Scout] Feature flags:', { enableChat, enablePlaybooks });
  }, [enableChat, enablePlaybooks]);

  // Don't render until subdomain URLs are set on client side
  if (Object.keys(subdomainUrls).length === 0) {
    return (
      <div className="bg-white/60 backdrop-blur-sm border-2 border-gray-200/50 dark:bg-gray-800/60 dark:border-gray-700/50 rounded-3xl p-8 shadow-lg mb-6">
        <div className="animate-pulse">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className="h-32 bg-gray-100 dark:bg-gray-700 rounded-2xl"></div>
            <div className="h-32 bg-gray-100 dark:bg-gray-700 rounded-2xl"></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Core Services */}
      <div className="bg-white/60 backdrop-blur-sm border-2 border-gray-200/50 dark:bg-gray-800/60 dark:border-gray-700/50 rounded-3xl p-8 shadow-lg">
        <div className="text-center mb-6">
          <div className="flex items-center justify-center gap-2 mb-2">
            <div className="w-8 h-8 rounded-lg bg-amber-500 flex items-center justify-center">
              <HiCube className="text-base text-white" />
            </div>
            <h2 className="text-sm font-extrabold text-gray-900 dark:text-white uppercase tracking-widest">
              Core Services
            </h2>
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-400 font-light">
            Essential tools for data exploration and analysis
          </p>
        </div>

        <ToolsGrid subdomainUrls={subdomainUrls} enableChat={enableChat} />

        {/* Documentation Link */}
        <div className="mt-6 text-center">
          <a
            href="https://washu-scout.readthedocs.io/en/latest/"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors duration-200 no-underline"
          >
            <SiReadthedocs className="text-base" />
            <span>New to Scout? Check out our documentation</span>
            <HiArrowRight className="text-base" />
          </a>
        </div>
      </div>

      {/* Playbooks & Admin Tools - Side by side when admin, stacked otherwise */}
      <AdminSection
        requireAdmin={true}
        fallback={
          /* Playbooks only (non-admin view) */
          enablePlaybooks && subdomainUrls.playbooks ? (
            <div className="bg-white/60 backdrop-blur-sm border-2 border-gray-200/50 dark:bg-gray-800/60 dark:border-gray-700/50 rounded-3xl p-8 shadow-lg">
              <div className="text-center mb-6">
                <div className="flex items-center justify-center gap-2 mb-3">
                  <div className="w-8 h-8 rounded-lg bg-green-600 flex items-center justify-center">
                    <HiOutlineBookOpen className="text-base text-white" />
                  </div>
                  <h2 className="text-sm font-extrabold text-gray-900 dark:text-white uppercase tracking-widest">
                    Playbooks
                  </h2>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400 font-light">
                  Pluggable workflows and dashboards
                </p>
              </div>
              <PlaybooksGrid playbooksUrl={subdomainUrls.playbooks} />
            </div>
          ) : null
        }
      >
        {/* Side-by-side layout for admins */}
        <div className={`grid gap-6 ${enablePlaybooks ? 'md:grid-cols-2' : 'grid-cols-1'}`}>
          {/* Playbooks */}
          {enablePlaybooks && subdomainUrls.playbooks && (
            <div className="bg-white/60 backdrop-blur-sm border-2 border-gray-200/50 dark:bg-gray-800/60 dark:border-gray-700/50 rounded-3xl p-8 shadow-lg h-full">
              <div className="text-center mb-6">
                <div className="flex items-center justify-center gap-2 mb-3">
                  <div className="w-8 h-8 rounded-lg bg-green-600 flex items-center justify-center">
                    <HiOutlineBookOpen className="text-base text-white" />
                  </div>
                  <h2 className="text-sm font-extrabold text-gray-900 dark:text-white uppercase tracking-widest">
                    Playbooks
                  </h2>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400 font-light">
                  Pluggable workflows and dashboards
                </p>
              </div>
              <PlaybooksGrid playbooksUrl={subdomainUrls.playbooks} />
            </div>
          )}

          {/* Admin Tools */}
          <div className="bg-white/60 backdrop-blur-sm border-2 border-gray-200/50 dark:bg-gray-800/60 dark:border-gray-700/50 rounded-3xl p-8 shadow-lg h-full flex flex-col">
            <div className="text-center mb-6">
              <div className="flex items-center justify-center gap-2 mb-3">
                <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
                  <HiCog className="text-base text-white" />
                </div>
                <h2 className="text-sm font-extrabold text-gray-900 dark:text-white uppercase tracking-widest">
                  Admin Tools
                </h2>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400 font-light">
                Infrastructure and user management
              </p>
            </div>
            <div className="grid grid-cols-2 gap-4 flex-1">
              <a
                href={subdomainUrls.minio}
                target="_blank"
                rel="noopener noreferrer"
                className="group relative p-4 bg-gradient-to-br from-slate-50 to-gray-50 dark:from-gray-800 dark:to-gray-900 rounded-2xl hover:bg-gradient-to-br hover:from-red-50 hover:to-rose-50 dark:hover:from-red-900/20 dark:hover:to-rose-900/10 border border-slate-200/50 dark:border-gray-700 hover:border-red-300 dark:hover:border-red-600 hover:shadow-xl hover:shadow-red-200/30 dark:hover:shadow-red-500/20 hover:-translate-y-1 transition-all duration-300 no-underline text-center overflow-hidden flex flex-col items-center justify-center"
              >
                <div className="relative w-16 h-16 rounded-xl bg-white dark:bg-gray-800 shadow-md flex items-center justify-center mb-2 group-hover:scale-110 transition-transform duration-300">
                  <SiMinio className="text-4xl text-red-600 dark:text-red-400" />
                </div>
                <div className="relative text-sm font-bold text-gray-900 dark:text-white">Lake</div>
              </a>

              <a
                href={subdomainUrls.temporal}
                target="_blank"
                rel="noopener noreferrer"
                className="group relative p-4 bg-gradient-to-br from-slate-50 to-gray-50 dark:from-gray-800 dark:to-gray-900 rounded-2xl hover:bg-gradient-to-br hover:from-cyan-50 hover:to-sky-50 dark:hover:from-cyan-900/20 dark:hover:to-sky-900/10 border border-slate-200/50 dark:border-gray-700 hover:border-cyan-300 dark:hover:border-cyan-600 hover:shadow-xl hover:shadow-cyan-200/30 dark:hover:shadow-cyan-500/20 hover:-translate-y-1 transition-all duration-300 no-underline text-center overflow-hidden flex flex-col items-center justify-center"
              >
                <div className="relative w-16 h-16 rounded-xl bg-white dark:bg-gray-800 shadow-md flex items-center justify-center mb-2 group-hover:scale-110 transition-transform duration-300">
                  <SiTemporal className="text-4xl text-cyan-600 dark:text-cyan-400" />
                </div>
                <div className="relative text-sm font-bold text-gray-900 dark:text-white">
                  Orchestrator
                </div>
              </a>

              <a
                href={subdomainUrls.grafana}
                target="_blank"
                rel="noopener noreferrer"
                className="group relative p-4 bg-gradient-to-br from-slate-50 to-gray-50 dark:from-gray-800 dark:to-gray-900 rounded-2xl hover:bg-gradient-to-br hover:from-orange-50 hover:to-amber-50 dark:hover:from-orange-900/20 dark:hover:to-amber-900/10 border border-slate-200/50 dark:border-gray-700 hover:border-orange-300 dark:hover:border-orange-600 hover:shadow-xl hover:shadow-orange-200/30 dark:hover:shadow-orange-500/20 hover:-translate-y-1 transition-all duration-300 no-underline text-center overflow-hidden flex flex-col items-center justify-center"
              >
                <div className="relative w-16 h-16 rounded-xl bg-white dark:bg-gray-800 shadow-md flex items-center justify-center mb-2 group-hover:scale-110 transition-transform duration-300">
                  <SiGrafana className="text-4xl text-orange-500 dark:text-orange-400" />
                </div>
                <div className="relative text-sm font-bold text-gray-900 dark:text-white">
                  Monitor
                </div>
              </a>

              <a
                href={subdomainUrls.keycloak}
                target="_blank"
                rel="noopener noreferrer"
                className="group relative p-4 bg-gradient-to-br from-slate-50 to-gray-50 dark:from-gray-800 dark:to-gray-900 rounded-2xl hover:bg-gradient-to-br hover:from-blue-50 hover:to-indigo-50 dark:hover:from-blue-900/20 dark:hover:to-indigo-900/10 border border-slate-200/50 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 hover:shadow-xl hover:shadow-blue-200/30 dark:hover:shadow-blue-500/20 hover:-translate-y-1 transition-all duration-300 no-underline text-center overflow-hidden flex flex-col items-center justify-center"
              >
                <div className="relative w-16 h-16 rounded-xl bg-white dark:bg-gray-800 shadow-md flex items-center justify-center mb-2 group-hover:scale-110 transition-transform duration-300">
                  <SiKeycloak className="text-4xl text-blue-600 dark:text-blue-400" />
                </div>
                <div className="relative text-sm font-bold text-gray-900 dark:text-white">
                  Users
                </div>
              </a>
            </div>
          </div>
        </div>
      </AdminSection>
    </div>
  );
};

interface HomeClientProps {
  enableChat: boolean;
  enablePlaybooks: boolean;
}

export default function HomeClient({ enableChat, enablePlaybooks }: HomeClientProps) {
  const [mounted, setMounted] = useState(false);
  const { data: session, status } = useSession();

  useEffect(() => {
    setMounted(true);
    console.log('[Scout Client] Props received:', { enableChat, enablePlaybooks });
  }, [enableChat, enablePlaybooks]);

  // Auto-login: redirect to sign in if not authenticated
  useEffect(() => {
    if (status !== 'loading' && !session) {
      signIn('keycloak');
    }
  }, [status, session]);

  // Show loading state while checking auth or redirecting to login
  if (status === 'loading' || !session) {
    return (
      <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:bg-gradient-to-br dark:from-gray-900 dark:via-gray-900 dark:to-gray-800 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block p-1 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 mb-4 shadow-lg shadow-blue-200 dark:shadow-blue-900/50">
            <img src="/scout.png" alt="Scout" className="h-16 rounded-xl bg-white p-2" />
          </div>
          <div className="flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400">
            <div
              className="w-2 h-2 bg-blue-500 rounded-full animate-bounce"
              style={{ animationDelay: '0ms' }}
            ></div>
            <div
              className="w-2 h-2 bg-blue-500 rounded-full animate-bounce"
              style={{ animationDelay: '150ms' }}
            ></div>
            <div
              className="w-2 h-2 bg-blue-500 rounded-full animate-bounce"
              style={{ animationDelay: '300ms' }}
            ></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:bg-gradient-to-br dark:from-gray-900 dark:via-gray-900 dark:to-gray-800 transition-colors duration-500 flex items-center justify-center py-12">
      {/* Floating TopBar */}
      <div className="absolute top-0 right-0 p-6 z-10">
        <TopBar />
      </div>

      <div
        className={`w-full max-w-6xl px-6 transition-all duration-700 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
      >
        {/* Hero Section */}
        <div className="text-center mb-12">
          <div className="inline-block p-1 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 mb-2 shadow-lg shadow-blue-200 dark:shadow-blue-900/50">
            <img src="/scout.png" alt="Scout" className="h-16 rounded-xl bg-white p-2" />
          </div>
          <h1 className="text-5xl md:text-6xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-gray-900 via-gray-800 to-gray-900 dark:text-white mb-4 tracking-tight leading-tight">
            Welcome to Scout
          </h1>
          <p className="text-lg md:text-xl text-gray-600 dark:text-gray-300 max-w-4xl mx-auto leading-relaxed font-light">
            A data exploration and clinical insights platform brought to you by the <br />{' '}
            Mallinckrodt Institute of Radiology&apos;s{' '}
            <span className="font-semibold text-gray-800 dark:text-gray-100">
              Translational AI Group
            </span>{' '}
            <br /> at Washington University in St. Louis.
          </p>
        </div>

        {/* Content Grid */}
        <ContentGrid enableChat={enableChat} enablePlaybooks={enablePlaybooks} />

        {/* Footer */}
        <div className="text-center mt-12 pt-6 border-t border-gray-200 dark:border-gray-700">
          <p className="text-sm text-gray-500 dark:text-gray-400 font-light">
            © {new Date().getFullYear()}{' '}
            <span className="font-medium">Translational AI Group</span> • Mallinckrodt Institute of
            Radiology • Washington University in St. Louis
          </p>
        </div>
      </div>
    </div>
  );
}
