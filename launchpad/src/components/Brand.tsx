import { Fragment, ReactNode } from 'react';

// Shared brand strip (logo + "Scout" / <crumb> / <crumb> …) so the launchpad
// home and the admin console can't drift apart. `crumbs` are the breadcrumb
// segments after the logo — [environment] on home, [environment, "Users"] on the
// console, so the console trail is a consistent extension of home's rather than
// swapping its second segment. `leading` is an optional slot before the logo
// (the console uses it for a back-to-launchpad arrow).
export default function Brand({ crumbs, leading }: { crumbs: ReactNode[]; leading?: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5">
      {leading}
      <div className="p-0.5 rounded-md bg-gradient-to-br from-indigo-500 to-indigo-700">
        <img src="/scout.png" alt="Scout" className="h-7 w-7 rounded bg-white p-0.5 block" />
      </div>
      <span className="text-sm font-semibold text-slate-800 dark:text-slate-100 tracking-tight">
        Scout
      </span>
      {crumbs.map((crumb, i) => (
        <Fragment key={i}>
          <span className="text-slate-300 dark:text-slate-700 text-sm">/</span>
          <span className="text-sm text-slate-500 dark:text-slate-400">{crumb}</span>
        </Fragment>
      ))}
    </div>
  );
}
