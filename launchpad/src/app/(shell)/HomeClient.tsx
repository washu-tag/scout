'use client';

import React, { useState, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import CatalogSections from '@/components/catalog/CatalogSections';
import type { RenderModel } from '@/lib/catalog/types';

interface HomeClientProps {
  model: RenderModel;
  environment: string;
  deployerName?: string;
}

export default function HomeClient({ model, environment, deployerName }: HomeClientProps) {
  const [mounted, setMounted] = useState(false);
  const { data: session } = useSession();
  const firstName = session?.user?.name?.split(' ')[0] || session?.user?.username;

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <div className="min-h-full w-full bg-slate-50 dark:bg-slate-950 transition-colors duration-500 flex justify-center px-4 py-8 md:px-6 lg:px-8">
      <div
        className={`w-full max-w-content flex flex-col gap-8 transition-all duration-700 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
      >
        <header className="flex flex-col gap-2.5">
          <span className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-indigo-700 dark:text-indigo-300">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 dark:bg-emerald-400" />
            Scout · {environment}
          </span>
          <h1 className="text-3xl sm:text-4xl leading-[1.05] font-semibold tracking-tight text-slate-900 dark:text-slate-100">
            Welcome{firstName ? `, ${firstName}` : ''}.
          </h1>
        </header>

        {/* Sections and chips come from the runtime catalog (ADR 0034), already
            audience-filtered and link-resolved server-side. */}
        <CatalogSections model={model} />

        <footer className="pt-4 border-t border-slate-200 dark:border-slate-800 text-sm text-slate-500 dark:text-slate-400">
          Developed at{' '}
          <a
            href="https://github.com/washu-tag/scout"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white underline-offset-2 hover:underline"
          >
            Washington University in St. Louis
          </a>
          {deployerName && (
            <>
              {' · Deployed by '}
              <span className="font-medium text-slate-600 dark:text-slate-300">{deployerName}</span>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
