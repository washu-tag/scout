import React from 'react';
import Link from 'next/link';
import { DEFAULT_ICON, ICONS } from '@/lib/catalog/icons';
import { TONES, type ToneName } from '@/lib/catalog/tones';

export interface Crumb {
  label: string;
  href?: string;
}

export const BAR_TOOL =
  'inline-flex h-[34px] items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[13.5px] font-medium text-slate-700 no-underline transition-colors hover:border-slate-300 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-slate-600 dark:hover:text-white';

// The app bar standard: docs/source/customize/launchpad-app-bar.md.
export default function AppBar({
  icon,
  tone,
  crumbs,
  children,
}: {
  icon: string;
  tone: ToneName;
  crumbs: [...Crumb[], Crumb];
  children?: React.ReactNode;
}) {
  const Icon = ICONS[icon] ?? ICONS[DEFAULT_ICON];
  const current = crumbs[crumbs.length - 1];
  return (
    <header className="flex h-14 flex-none items-center gap-2.5 border-b border-slate-200 bg-white px-8 dark:border-slate-800 dark:bg-slate-900">
      <span
        className={`flex h-7 w-7 flex-none items-center justify-center rounded-lg ${TONES[tone].iconBg} ${TONES[tone].icon}`}
      >
        <Icon className="h-4 w-4" aria-hidden="true" />
      </span>
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2.5 text-[15px]">
        {crumbs.slice(0, -1).map((crumb) => (
          <React.Fragment key={crumb.label}>
            {crumb.href ? (
              <Link
                href={crumb.href}
                className="min-w-0 truncate font-medium text-slate-600 no-underline hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
              >
                {crumb.label}
              </Link>
            ) : (
              <span className="min-w-0 truncate font-medium text-slate-600 dark:text-slate-400">
                {crumb.label}
              </span>
            )}
            <span aria-hidden="true" className="flex-none text-slate-400 dark:text-slate-600">
              /
            </span>
          </React.Fragment>
        ))}
        <h1 className="min-w-0 truncate font-semibold text-slate-900 dark:text-white">
          {current.label}
        </h1>
      </nav>
      {children && <div className="ml-auto flex flex-none items-center gap-2.5">{children}</div>}
    </header>
  );
}
