import { ReactNode } from 'react';

// Shared brand strip (logo + "Scout" / <tail>) so the launchpad home and the
// admin console can't drift apart. `tail` is the breadcrumb segment after the
// slash — the environment name on home, "Users" on the console. `leading` is an
// optional slot before the logo (the console uses it for a back-to-launchpad arrow).
export default function Brand({ tail, leading }: { tail: ReactNode; leading?: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5">
      {leading}
      <div className="p-0.5 rounded-md bg-gradient-to-br from-indigo-500 to-indigo-700">
        <img src="/scout.png" alt="Scout" className="h-7 w-7 rounded bg-white p-0.5 block" />
      </div>
      <span className="text-sm font-semibold text-slate-800 dark:text-slate-100 tracking-tight">
        Scout
      </span>
      <span className="text-slate-300 dark:text-slate-700 text-sm">/</span>
      <span className="text-sm text-slate-500 dark:text-slate-400">{tail}</span>
    </div>
  );
}
