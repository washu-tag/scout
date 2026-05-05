import {
  HiUserGroup,
  HiChartBar,
  HiSparkles,
  HiClipboardCheck,
  HiDocumentText,
  HiBeaker,
} from 'react-icons/hi';
import { IconType } from 'react-icons';

/**
 * Icon mapping for dynamic playbooks.
 * Maps string icon names to React icon components.
 */
export const ICON_MAP: Record<string, IconType> = {
  users: HiUserGroup,
  chart: HiChartBar,
  sparkles: HiSparkles,
  clipboard: HiClipboardCheck,
  document: HiDocumentText,
  beaker: HiBeaker,
};

/**
 * Color configuration for dynamic playbooks.
 * Each color contributes the icon tint, CTA accent, and hover border/shadow — card surface stays neutral.
 */
export const COLOR_MAP: Record<
  string,
  {
    iconBg: string;
    icon: string;
    cta: string;
    hoverBorder: string;
    hoverShadow: string;
  }
> = {
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
  blue: {
    iconBg: 'bg-blue-50 border-blue-100 dark:bg-blue-950/40 dark:border-blue-900/50',
    icon: 'text-blue-600 dark:text-blue-400',
    cta: 'text-blue-600 dark:text-blue-400',
    hoverBorder: 'hover:border-blue-200 dark:hover:border-blue-900/60',
    hoverShadow: 'hover:shadow-blue-200/50 dark:hover:shadow-blue-500/15',
  },
  indigo: {
    iconBg: 'bg-indigo-50 border-indigo-100 dark:bg-indigo-950/40 dark:border-indigo-900/50',
    icon: 'text-indigo-600 dark:text-indigo-400',
    cta: 'text-indigo-600 dark:text-indigo-400',
    hoverBorder: 'hover:border-indigo-200 dark:hover:border-indigo-900/60',
    hoverShadow: 'hover:shadow-indigo-200/50 dark:hover:shadow-indigo-500/15',
  },
  pink: {
    iconBg: 'bg-pink-50 border-pink-100 dark:bg-pink-950/40 dark:border-pink-900/50',
    icon: 'text-pink-600 dark:text-pink-400',
    cta: 'text-pink-600 dark:text-pink-400',
    hoverBorder: 'hover:border-pink-200 dark:hover:border-pink-900/60',
    hoverShadow: 'hover:shadow-pink-200/50 dark:hover:shadow-pink-500/15',
  },
};

/**
 * Default color to use when an unknown color is specified.
 */
export const DEFAULT_COLOR = 'violet';

/**
 * Default icon to use when an unknown icon is specified.
 */
export const DEFAULT_ICON = 'chart';

/**
 * Get icon component for a playbook.
 */
export function getPlaybookIcon(iconName: string): IconType {
  return ICON_MAP[iconName] || ICON_MAP[DEFAULT_ICON];
}

/**
 * Get color configuration for a playbook.
 */
export function getPlaybookColors(colorName: string): (typeof COLOR_MAP)[keyof typeof COLOR_MAP] {
  return COLOR_MAP[colorName] || COLOR_MAP[DEFAULT_COLOR];
}
