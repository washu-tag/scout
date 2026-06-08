import { Fragment, ReactNode } from 'react';

// Shared brand strip (logo + "Scout" / <crumb> / <crumb> …) so the launchpad
// home and the admin console can't drift apart. The logo + "Scout" link home
// and are the first element on every page, so the logo sits in the exact same
// spot whether you're on home or a sub-page (no jump on navigation). `crumbs`
// are the breadcrumb segments after the wordmark — [environment] on home,
// [environment, "Users"] on the console.
export default function Brand({ crumbs }: { crumbs: ReactNode[] }) {
  return (
    <div className="flex items-center gap-2.5">
      <a
        href="/"
        aria-label="Scout home"
        className="flex items-center gap-2.5 rounded-md hover:opacity-80 transition-opacity"
      >
        <div className="p-0.5 rounded-md bg-gradient-to-br from-indigo-500 to-indigo-700">
          <img src="/scout.png" alt="Scout" className="h-7 w-7 rounded bg-white p-0.5 block" />
        </div>
        <span className="text-sm font-semibold text-slate-800 dark:text-slate-100 tracking-tight">
          Scout
        </span>
      </a>
      {crumbs.map((crumb, i) => (
        <Fragment key={i}>
          <span className="text-slate-300 dark:text-slate-700 text-sm">/</span>
          <span className="text-sm text-slate-500 dark:text-slate-400">{crumb}</span>
        </Fragment>
      ))}
    </div>
  );
}
