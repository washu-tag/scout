'use client';

import React, { useState, useEffect } from 'react';
import CatalogSections from '@/components/catalog/CatalogSections';
import type { RenderModel } from '@/lib/catalog/types';

interface HomeClientProps {
  model: RenderModel;
  deployerName?: string;
}

export default function HomeClient({ model, deployerName }: HomeClientProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <div className="min-h-full w-full bg-gradient-to-br from-slate-50 via-white to-indigo-50/40 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/30 transition-colors duration-500 flex items-center justify-center py-12">
      <div
        className={`w-full max-w-content px-6 transition-all duration-700 ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
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
