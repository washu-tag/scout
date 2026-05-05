'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useSession, signIn } from 'next-auth/react';
import { SiGrafana, SiKeycloak, SiMinio, SiTemporal, SiPython } from 'react-icons/si';
import {
  HiArrowRight,
  HiCube,
  HiCog,
  HiOutlineBookOpen,
  HiOutlineDocumentText,
  HiUserGroup,
  HiChartBar,
  HiSparkles,
  HiOutlineChartBar,
  HiOutlineChat,
  HiOutlineSearch,
} from 'react-icons/hi';
import { getPlaybookIcon, getPlaybookColors } from '@/lib/playbook-config';
import TopBar from '@/components/TopBar';
import AdminSection from '@/components/AdminSection';

type ServiceTone = 'indigo' | 'emerald' | 'amber';

const SERVICE_TONE: Record<
  ServiceTone,
  {
    iconBg: string;
    icon: string;
    cta: string;
    hoverBorder: string;
    hoverShadow: string;
  }
> = {
  indigo: {
    iconBg: 'bg-indigo-50 border-indigo-100 dark:bg-indigo-950/40 dark:border-indigo-900/50',
    icon: 'text-indigo-600 dark:text-indigo-400',
    cta: 'text-indigo-600 dark:text-indigo-400',
    hoverBorder: 'hover:border-indigo-200 dark:hover:border-indigo-900/60',
    hoverShadow: 'hover:shadow-indigo-200/50 dark:hover:shadow-indigo-500/15',
  },
  emerald: {
    iconBg: 'bg-emerald-50 border-emerald-100 dark:bg-emerald-950/40 dark:border-emerald-900/50',
    icon: 'text-emerald-600 dark:text-emerald-400',
    cta: 'text-emerald-600 dark:text-emerald-400',
    hoverBorder: 'hover:border-emerald-200 dark:hover:border-emerald-900/60',
    hoverShadow: 'hover:shadow-emerald-200/50 dark:hover:shadow-emerald-500/15',
  },
  amber: {
    iconBg: 'bg-amber-50 border-amber-100 dark:bg-amber-950/40 dark:border-amber-900/50',
    icon: 'text-amber-600 dark:text-amber-400',
    cta: 'text-amber-600 dark:text-amber-400',
    hoverBorder: 'hover:border-amber-200 dark:hover:border-amber-900/60',
    hoverShadow: 'hover:shadow-amber-200/50 dark:hover:shadow-amber-500/15',
  },
};

interface ServiceCardProps {
  href: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  tone: ServiceTone;
  external?: boolean;
}

const ServiceCard = ({
  href,
  icon,
  title,
  description,
  tone,
  external = false,
}: ServiceCardProps) => {
  const t = SERVICE_TONE[tone];
  return (
    <a
      href={href}
      {...(external && { target: '_blank' })}
      className={`group block p-6 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl transition-all duration-200 no-underline hover:shadow-lg hover:-translate-y-0.5 ${t.hoverBorder} ${t.hoverShadow}`}
    >
      <div className="flex items-center gap-3 mb-4">
        <div
          className={`w-11 h-11 rounded-xl border flex items-center justify-center transition-colors duration-200 ${t.iconBg}`}
        >
          <div className={`text-xl ${t.icon}`}>{icon}</div>
        </div>
        <h3 className="text-xl font-semibold text-slate-900 dark:text-white tracking-tight">
          {title}
        </h3>
      </div>
      <p className="text-base text-slate-500 dark:text-slate-400 mb-4 leading-relaxed font-light">
        {description}
      </p>
      <div className={`flex items-center gap-1 font-medium text-sm ${t.cta}`}>
        <span className="group-hover:translate-x-0.5 transition-transform duration-200">Open</span>
        <HiArrowRight className="transform group-hover:translate-x-1 transition-transform duration-200 ease-out" />
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
        icon={<HiOutlineChartBar />}
        title="Analytics"
        description="Visual dashboards, business intelligence, and SQL queries"
        tone="amber"
        external={true}
      />

      {enableChat && (
        <ServiceCard
          href={subdomainUrls.chat}
          icon={<HiOutlineChat />}
          title="Chat"
          description="AI-powered assistance for Q & A style data queries"
          tone="emerald"
          external={true}
        />
      )}

      <ServiceCard
        href={subdomainUrls.jupyter}
        icon={<SiPython />}
        title="Notebooks"
        description="Interactive data analysis and coding with Jupyter"
        tone="indigo"
        external={true}
      />
    </div>
  );
};

// Type for dynamic playbooks loaded from API
interface DynamicPlaybook {
  id: string;
  title: string;
  description: string;
  notebook: string;
  icon: string;
  color: string;
}

// Playbook definitions with icons and colors (hardcoded/static playbooks)
const STATIC_PLAYBOOKS = [
  {
    id: 'cohort',
    title: 'Research Cohorting',
    description: 'Build and manage patient cohorts for research studies',
    notebook: 'Cohort.ipynb',
    icon: HiUserGroup,
    color: 'violet',
  },
  {
    id: 'rads',
    title: 'RADS Dashboard',
    description: 'Explore LI-RADS and BI-RADS reporting trends over time',
    notebook: 'RADS.ipynb',
    icon: HiChartBar,
    color: 'rose',
  },
  {
    id: 'followup-detection',
    title: 'Clinical Follow-up Monitoring',
    description: 'Review algorithmically-detected follow-up recommendations',
    notebook: 'FollowUpDetection.ipynb',
    icon: HiSparkles,
    color: 'cyan',
  },
];

interface PlaybooksGridProps {
  playbooksUrl: string;
  dynamicPlaybooks: DynamicPlaybook[];
}

interface PlaybookRowProps {
  href: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  colors: {
    iconBg: string;
    icon: string;
    cta: string;
    hoverBorder: string;
    hoverShadow: string;
  };
}

const PlaybookRow = ({ href, title, description, icon, colors }: PlaybookRowProps) => (
  <a
    href={href}
    target="_blank"
    rel="noopener noreferrer"
    className={`group flex items-center gap-3 p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 no-underline ${colors.hoverBorder} ${colors.hoverShadow}`}
  >
    <div
      className={`w-10 h-10 rounded-lg border flex items-center justify-center flex-shrink-0 ${colors.iconBg}`}
    >
      <div className={`text-xl ${colors.icon}`}>{icon}</div>
    </div>
    <div className="flex-1 min-w-0">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-white tracking-tight">
        {title}
      </h3>
      <p className="text-xs text-slate-500 dark:text-slate-400 leading-snug font-light">
        {description}
      </p>
    </div>
    <HiArrowRight
      className={`text-base ${colors.cta} group-hover:translate-x-1 transition-transform duration-200 flex-shrink-0`}
    />
  </a>
);

const PlaybooksGrid = ({ playbooksUrl, dynamicPlaybooks }: PlaybooksGridProps) => {
  return (
    <div className="space-y-3">
      {STATIC_PLAYBOOKS.map((playbook) => {
        const IconComponent = playbook.icon;
        return (
          <PlaybookRow
            key={playbook.id}
            href={`${playbooksUrl}/voila/render/${playbook.id}/${playbook.notebook}`}
            title={playbook.title}
            description={playbook.description}
            icon={<IconComponent />}
            colors={getPlaybookColors(playbook.color)}
          />
        );
      })}

      {dynamicPlaybooks.map((playbook) => {
        const IconComponent = getPlaybookIcon(playbook.icon);
        return (
          <PlaybookRow
            key={playbook.id}
            href={`${playbooksUrl}/voila/render/${playbook.id}/${playbook.notebook}`}
            title={playbook.title}
            description={playbook.description}
            icon={<IconComponent />}
            colors={getPlaybookColors(playbook.color)}
          />
        );
      })}
    </div>
  );
};

interface ContentGridProps {
  enableChat: boolean;
  enablePlaybooks: boolean;
  subdomainUrls: Record<string, string>;
}

const ContentGrid = ({ enableChat, enablePlaybooks, subdomainUrls }: ContentGridProps) => {
  const [dynamicPlaybooks, setDynamicPlaybooks] = useState<DynamicPlaybook[]>([]);

  // Fetch dynamic playbooks from API
  useEffect(() => {
    if (!enablePlaybooks) return;

    const fetchPlaybooks = async () => {
      try {
        const response = await fetch('/api/playbooks');
        if (response.ok) {
          const playbooks = await response.json();
          setDynamicPlaybooks(playbooks);
          console.debug('[Scout] Loaded dynamic playbooks:', playbooks.length);
        }
      } catch (error) {
        console.error('[Scout] Failed to fetch dynamic playbooks:', error);
      }
    };

    fetchPlaybooks();
  }, [enablePlaybooks]);

  // Don't render until subdomain URLs are set on client side
  if (Object.keys(subdomainUrls).length === 0) {
    return (
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-8 shadow-sm mb-6">
        <div className="animate-pulse">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className="h-32 bg-slate-100 dark:bg-slate-800 rounded-2xl"></div>
            <div className="h-32 bg-slate-100 dark:bg-slate-800 rounded-2xl"></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Core Services */}
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-8 shadow-sm">
        <div className="text-center mb-6">
          <div className="flex items-center justify-center gap-2 mb-2">
            <div className="w-7 h-7 rounded-md bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
              <HiCube className="text-sm text-slate-600 dark:text-slate-300" />
            </div>
            <h2 className="text-xs font-semibold text-slate-700 dark:text-slate-200 uppercase tracking-[0.18em]">
              Core Services
            </h2>
          </div>
          <p className="text-sm text-slate-500 dark:text-slate-400 font-light">
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
            className="inline-flex items-center gap-2 px-4 py-2 text-sm text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors duration-200 no-underline"
          >
            <HiOutlineDocumentText className="text-base" />
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
            <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-8 shadow-sm">
              <div className="text-center mb-6">
                <div className="flex items-center justify-center gap-2 mb-3">
                  <div className="w-7 h-7 rounded-md bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
                    <HiOutlineBookOpen className="text-sm text-slate-600 dark:text-slate-300" />
                  </div>
                  <h2 className="text-xs font-semibold text-slate-700 dark:text-slate-200 uppercase tracking-[0.18em]">
                    Playbooks
                  </h2>
                </div>
                <p className="text-sm text-slate-500 dark:text-slate-400 font-light">
                  Pluggable workflows and dashboards
                </p>
              </div>
              <PlaybooksGrid
                playbooksUrl={subdomainUrls.playbooks}
                dynamicPlaybooks={dynamicPlaybooks}
              />
            </div>
          ) : null
        }
      >
        {/* Side-by-side layout for admins */}
        <div className={`grid gap-6 ${enablePlaybooks ? 'md:grid-cols-2' : 'grid-cols-1'}`}>
          {/* Playbooks */}
          {enablePlaybooks && subdomainUrls.playbooks && (
            <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-8 shadow-sm h-full">
              <div className="text-center mb-6">
                <div className="flex items-center justify-center gap-2 mb-3">
                  <div className="w-7 h-7 rounded-md bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
                    <HiOutlineBookOpen className="text-sm text-slate-600 dark:text-slate-300" />
                  </div>
                  <h2 className="text-xs font-semibold text-slate-700 dark:text-slate-200 uppercase tracking-[0.18em]">
                    Playbooks
                  </h2>
                </div>
                <p className="text-sm text-slate-500 dark:text-slate-400 font-light">
                  Pluggable workflows and dashboards
                </p>
              </div>
              <PlaybooksGrid
                playbooksUrl={subdomainUrls.playbooks}
                dynamicPlaybooks={dynamicPlaybooks}
              />
            </div>
          )}

          {/* Admin Tools */}
          <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-8 shadow-sm h-full flex flex-col">
            <div className="text-center mb-6">
              <div className="flex items-center justify-center gap-2 mb-3">
                <div className="w-7 h-7 rounded-md bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
                  <HiCog className="text-sm text-slate-600 dark:text-slate-300" />
                </div>
                <h2 className="text-xs font-semibold text-slate-700 dark:text-slate-200 uppercase tracking-[0.18em]">
                  Admin Tools
                </h2>
              </div>
              <p className="text-sm text-slate-500 dark:text-slate-400 font-light">
                Infrastructure and user management
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3 flex-1">
              {[
                {
                  href: subdomainUrls.minio,
                  label: 'Lake',
                  description: 'Medical data lake storage',
                  Icon: SiMinio,
                  iconBg: 'bg-red-50 border-red-100 dark:bg-red-950/40 dark:border-red-900/50',
                  iconColor: 'text-red-600 dark:text-red-400',
                  hoverBorder: 'hover:border-red-200 dark:hover:border-red-900/60',
                  hoverShadow: 'hover:shadow-red-200/50 dark:hover:shadow-red-500/15',
                },
                {
                  href: subdomainUrls.temporal,
                  label: 'Orchestrator',
                  description: 'Ingestion and characterization workflows',
                  Icon: SiTemporal,
                  iconBg: 'bg-cyan-50 border-cyan-100 dark:bg-cyan-950/40 dark:border-cyan-900/50',
                  iconColor: 'text-cyan-600 dark:text-cyan-400',
                  hoverBorder: 'hover:border-cyan-200 dark:hover:border-cyan-900/60',
                  hoverShadow: 'hover:shadow-cyan-200/50 dark:hover:shadow-cyan-500/15',
                },
                {
                  href: subdomainUrls.grafana,
                  label: 'Monitor',
                  description: 'Metrics, logs, and dashboards',
                  Icon: SiGrafana,
                  iconBg:
                    'bg-orange-50 border-orange-100 dark:bg-orange-950/40 dark:border-orange-900/50',
                  iconColor: 'text-orange-500 dark:text-orange-400',
                  hoverBorder: 'hover:border-orange-200 dark:hover:border-orange-900/60',
                  hoverShadow: 'hover:shadow-orange-200/50 dark:hover:shadow-orange-500/15',
                },
                {
                  href: subdomainUrls.keycloak,
                  label: 'Users',
                  description: 'Authentication and identity',
                  Icon: SiKeycloak,
                  iconBg: 'bg-blue-50 border-blue-100 dark:bg-blue-950/40 dark:border-blue-900/50',
                  iconColor: 'text-blue-600 dark:text-blue-400',
                  hoverBorder: 'hover:border-blue-200 dark:hover:border-blue-900/60',
                  hoverShadow: 'hover:shadow-blue-200/50 dark:hover:shadow-blue-500/15',
                },
              ].map((tool) => (
                <a
                  key={tool.label}
                  href={tool.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`group flex items-center gap-3 p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 no-underline ${tool.hoverBorder} ${tool.hoverShadow}`}
                >
                  <div
                    className={`w-10 h-10 rounded-lg border flex items-center justify-center flex-shrink-0 ${tool.iconBg}`}
                  >
                    <tool.Icon className={`text-xl ${tool.iconColor}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="text-sm font-semibold text-slate-900 dark:text-white tracking-tight">
                      {tool.label}
                    </h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400 leading-snug font-light">
                      {tool.description}
                    </p>
                  </div>
                </a>
              ))}
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
  scoutEnv?: string;
}

export default function HomeClient({ enableChat, enablePlaybooks, scoutEnv }: HomeClientProps) {
  const [mounted, setMounted] = useState(false);
  const { data: session, status } = useSession();
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [searchValue, setSearchValue] = useState('');
  const [subdomainUrls, setSubdomainUrls] = useState<Record<string, string>>({});

  const environment = scoutEnv ?? 'local';

  // Generate all subdomain URLs once on client side where window is available.
  useEffect(() => {
    const protocol = window.location.protocol;
    const host = window.location.host;
    const getUrl = (subdomain: string, path: string = '') => {
      const normalizedPath = path ? (path.startsWith('/') ? path : `/${path}`) : '';
      return `${protocol}//${subdomain}.${host}${normalizedPath}`;
    };
    setSubdomainUrls({
      jupyter: getUrl('jupyter'),
      superset: getUrl('superset'),
      chat: getUrl('chat', '/oauth/oidc/login'),
      // Chat root for deep links with URL params (e.g. ?q=, ?models=, ?tools=).
      // See https://docs.openwebui.com/features/chat-conversations/chat-features/url-params/
      chatRoot: getUrl('chat'),
      playbooks: getUrl('playbooks'),
      minio: getUrl('minio'),
      temporal: getUrl('temporal', '/auth/sso'),
      grafana: getUrl('grafana'),
      keycloak: getUrl('keycloak', '/admin/scout/console'),
    });
  }, []);

  // Cmd/Ctrl+K focuses the search input.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    setMounted(true);
    console.log('[Scout Client] Props received:', { enableChat, enablePlaybooks });
  }, [enableChat, enablePlaybooks]);

  const skipAuth =
    process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_SKIP_AUTH === 'true';

  // Auto-login: redirect to sign in if not authenticated
  useEffect(() => {
    if (!skipAuth && status !== 'loading' && !session) {
      signIn('keycloak');
    }
  }, [status, session, skipAuth]);

  // Show loading state while checking auth or redirecting to login
  if (!skipAuth && (status === 'loading' || !session)) {
    return (
      <div className="min-h-screen w-full bg-slate-50 dark:bg-slate-950 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block p-1 rounded-2xl bg-gradient-to-br from-indigo-500 to-indigo-700 mb-4 shadow-md">
            <img src="/scout.png" alt="Scout" className="h-14 rounded-xl bg-white p-2" />
          </div>
          <div className="flex items-center justify-center gap-2 text-slate-500 dark:text-slate-400">
            <div
              className="w-1.5 h-1.5 bg-slate-400 dark:bg-slate-500 rounded-full animate-bounce"
              style={{ animationDelay: '0ms' }}
            ></div>
            <div
              className="w-1.5 h-1.5 bg-slate-400 dark:bg-slate-500 rounded-full animate-bounce"
              style={{ animationDelay: '150ms' }}
            ></div>
            <div
              className="w-1.5 h-1.5 bg-slate-400 dark:bg-slate-500 rounded-full animate-bounce"
              style={{ animationDelay: '300ms' }}
            ></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-indigo-50/40 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/30 transition-colors duration-500 flex items-center justify-center py-12">
      {/* Floating header — brand on left, TopBar on right */}
      <div className="absolute top-0 left-0 right-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-6 flex items-center justify-between">
          {/* Brand strip */}
          <div className="flex items-center gap-2.5">
            <div className="p-0.5 rounded-md bg-gradient-to-br from-indigo-500 to-indigo-700">
              <img src="/scout.png" alt="Scout" className="h-7 w-7 rounded bg-white p-0.5 block" />
            </div>
            <span className="text-sm font-semibold text-slate-800 dark:text-slate-100 tracking-tight">
              Scout
            </span>
            <span className="text-slate-300 dark:text-slate-700 text-sm">/</span>
            <span className="text-sm text-slate-500 dark:text-slate-400">{environment}</span>
          </div>
          <TopBar />
        </div>
      </div>

      <div
        className={`w-full max-w-7xl px-6 pt-12 transition-all duration-700 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
      >
        {/* Hero — search-first */}
        <div className="mb-14">
          {/* Search input — Enter redirects to Chat with the query prefilled and auto-submitted. */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const q = searchValue.trim();
              if (!q || !subdomainUrls.chatRoot) return;
              const url = `${subdomainUrls.chatRoot}/?q=${encodeURIComponent(q)}`;
              window.open(url, '_blank', 'noopener,noreferrer');
              setSearchValue('');
            }}
            className="relative max-w-2xl mx-auto"
          >
            <div className="relative group">
              <HiOutlineSearch className="absolute left-5 top-1/2 -translate-y-1/2 text-xl text-slate-400 dark:text-slate-500 pointer-events-none" />
              <input
                ref={searchInputRef}
                type="text"
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
                placeholder="Ask Scout…"
                className="w-full pl-14 pr-20 py-4 text-base bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 shadow-sm focus:outline-none focus:border-indigo-500 dark:focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100 dark:focus:ring-indigo-950 transition-all duration-200"
              />
              <kbd className="absolute right-4 top-1/2 -translate-y-1/2 hidden md:flex items-center gap-1 px-2 py-1 text-[0.7rem] font-medium text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-md">
                <span>⌘</span>
                <span>K</span>
              </kbd>
            </div>
          </form>

          {/* Starter prompts — each opens Chat with the prompt prefilled and auto-submitted. */}
          <div className="mt-5 flex flex-wrap items-center justify-center gap-2 max-w-3xl mx-auto">
            <span className="text-xs text-slate-400 dark:text-slate-500 mr-1">Try</span>
            {[
              {
                icon: HiOutlineChartBar,
                prompt: 'Show MR volume trends over the last 12 months',
                iconColor: 'text-amber-600 dark:text-amber-400',
              },
              {
                icon: HiOutlineChat,
                prompt: 'Find reports mentioning sepsis',
                iconColor: 'text-emerald-600 dark:text-emerald-400',
              },
              {
                icon: HiUserGroup,
                prompt: 'Build a cohort of patients with multiple chest CTs',
                iconColor: 'text-indigo-600 dark:text-indigo-400',
              },
              {
                icon: HiSparkles,
                prompt: 'How often do reports recommend follow-up?',
                iconColor: 'text-violet-600 dark:text-violet-400',
              },
            ].map((q) => {
              const Icon = q.icon;
              const ready = Boolean(subdomainUrls.chatRoot);
              const href = ready
                ? `${subdomainUrls.chatRoot}/?q=${encodeURIComponent(q.prompt)}`
                : '#';
              return (
                <a
                  key={q.prompt}
                  href={href}
                  target={ready ? '_blank' : undefined}
                  rel={ready ? 'noopener noreferrer' : undefined}
                  aria-disabled={!ready}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-full transition-all duration-200 no-underline ${
                    ready
                      ? 'text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-700 hover:text-slate-900 dark:hover:text-white'
                      : 'text-slate-400 dark:text-slate-600 pointer-events-none'
                  }`}
                >
                  <Icon className={`text-sm ${q.iconColor}`} />
                  {q.prompt}
                </a>
              );
            })}
          </div>
        </div>

        {/* Content Grid */}
        <ContentGrid
          enableChat={enableChat}
          enablePlaybooks={enablePlaybooks}
          subdomainUrls={subdomainUrls}
        />

        {/* Footer */}
        <div className="text-center mt-12 pt-6 border-t border-slate-200 dark:border-slate-800">
          <p className="text-sm text-slate-400 dark:text-slate-500 font-light">
            © {new Date().getFullYear()}{' '}
            <span className="font-medium text-slate-500 dark:text-slate-400">Embark Labs</span>
          </p>
        </div>
      </div>
    </div>
  );
}
