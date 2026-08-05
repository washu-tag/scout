// The closed tone palette (ADR 0034). Tailwind class strings must be
// enumerable at build time, so tones are named bundles — never interpolated —
// and each name carries its coordinated light + dark treatment.
export interface Tone {
  iconBg: string;
  icon: string;
  cta: string;
  hoverBorder: string;
  hoverShadow: string;
}

export const TONES = {
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
  violet: {
    iconBg: 'bg-violet-50 border-violet-100 dark:bg-violet-950/40 dark:border-violet-900/50',
    icon: 'text-violet-600 dark:text-violet-400',
    cta: 'text-violet-600 dark:text-violet-400',
    hoverBorder: 'hover:border-violet-200 dark:hover:border-violet-900/60',
    hoverShadow: 'hover:shadow-violet-200/50 dark:hover:shadow-violet-500/15',
  },
  rose: {
    iconBg: 'bg-rose-50 border-rose-100 dark:bg-rose-950/40 dark:border-rose-900/50',
    icon: 'text-rose-600 dark:text-rose-400',
    cta: 'text-rose-600 dark:text-rose-400',
    hoverBorder: 'hover:border-rose-200 dark:hover:border-rose-900/60',
    hoverShadow: 'hover:shadow-rose-200/50 dark:hover:shadow-rose-500/15',
  },
  cyan: {
    iconBg: 'bg-cyan-50 border-cyan-100 dark:bg-cyan-950/40 dark:border-cyan-900/50',
    icon: 'text-cyan-600 dark:text-cyan-400',
    cta: 'text-cyan-600 dark:text-cyan-400',
    hoverBorder: 'hover:border-cyan-200 dark:hover:border-cyan-900/60',
    hoverShadow: 'hover:shadow-cyan-200/50 dark:hover:shadow-cyan-500/15',
  },
  red: {
    iconBg: 'bg-red-50 border-red-100 dark:bg-red-950/40 dark:border-red-900/50',
    icon: 'text-red-600 dark:text-red-400',
    cta: 'text-red-600 dark:text-red-400',
    hoverBorder: 'hover:border-red-200 dark:hover:border-red-900/60',
    hoverShadow: 'hover:shadow-red-200/50 dark:hover:shadow-red-500/15',
  },
  // The Monitor tile has always used the 500-weight orange for contrast
  // against Grafana's logo color; kept that way here.
  orange: {
    iconBg: 'bg-orange-50 border-orange-100 dark:bg-orange-950/40 dark:border-orange-900/50',
    icon: 'text-orange-500 dark:text-orange-400',
    cta: 'text-orange-500 dark:text-orange-400',
    hoverBorder: 'hover:border-orange-200 dark:hover:border-orange-900/60',
    hoverShadow: 'hover:shadow-orange-200/50 dark:hover:shadow-orange-500/15',
  },
  slate: {
    iconBg: 'bg-slate-100 border-slate-200 dark:bg-slate-800 dark:border-slate-700',
    icon: 'text-slate-600 dark:text-slate-300',
    cta: 'text-slate-600 dark:text-slate-400',
    hoverBorder: 'hover:border-slate-300 dark:hover:border-slate-600',
    hoverShadow: 'hover:shadow-slate-200/50 dark:hover:shadow-slate-500/15',
  },
} as const satisfies Record<string, Tone>;

export type ToneName = keyof typeof TONES;

export const TONE_NAMES = Object.keys(TONES) as [ToneName, ...ToneName[]];

export const DEFAULT_TONE: ToneName = 'indigo';

export function isToneName(value: string): value is ToneName {
  return value in TONES;
}
