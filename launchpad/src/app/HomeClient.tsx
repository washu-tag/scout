'use client';

import React, { useState, useEffect } from 'react';
import { useSession, signIn } from 'next-auth/react';
import TopBar from '@/components/TopBar';
import Brand from '@/components/Brand';
import CatalogSections from '@/components/catalog/CatalogSections';
import type { RenderModel } from '@/lib/catalog/types';

interface HomeClientProps {
  model: RenderModel;
  scoutEnv?: string;
  deployerName?: string;
  docsUrl: string;
}

export default function HomeClient({ model, scoutEnv, deployerName, docsUrl }: HomeClientProps) {
  const [mounted, setMounted] = useState(false);
  const { data: session, status } = useSession();

  const environment = scoutEnv ?? 'local';

  useEffect(() => {
    setMounted(true);
  }, []);

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
        <div className="max-w-content mx-auto px-6 py-6 flex items-center justify-between">
          {/* Brand strip */}
          <Brand crumbs={[environment]} />
          <TopBar docsUrl={docsUrl} />
        </div>
      </div>

      <div
        className={`w-full max-w-content px-6 pt-12 transition-all duration-700 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
      >
        {/* Sections and chips come from the runtime catalog (ADR 0034), already
            audience-filtered and link-resolved server-side. */}
        <CatalogSections model={model} />

        {/* Footer */}
        <div className="text-center mt-12 pt-6 border-t border-slate-200 dark:border-slate-800">
          <p className="text-sm text-slate-400 dark:text-slate-500 font-light">
            Developed at{' '}
            <a
              href="https://github.com/washu-tag/scout"
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 underline-offset-2 hover:underline"
            >
              Washington University in St. Louis
            </a>
            {deployerName && (
              <>
                {' · Deployed by '}
                <span className="font-medium text-slate-500 dark:text-slate-400">
                  {deployerName}
                </span>
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}
