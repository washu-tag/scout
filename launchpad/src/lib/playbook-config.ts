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
 * Maps color names to Tailwind CSS classes.
 */
export const COLOR_MAP: Record<
  string,
  {
    gradient: string;
    border: string;
    hoverBorder: string;
    shadow: string;
    icon: string;
    arrow: string;
  }
> = {
  violet: {
    gradient: 'from-violet-50 to-purple-50 dark:from-violet-900/20 dark:to-purple-900/10',
    border: 'border-violet-200/50 dark:border-violet-800/50',
    hoverBorder: 'hover:border-violet-400 dark:hover:border-violet-500',
    shadow: 'hover:shadow-violet-200/50 dark:hover:shadow-violet-500/30',
    icon: 'text-violet-600 dark:text-violet-400',
    arrow: 'group-hover:text-violet-600 dark:group-hover:text-violet-400',
  },
  rose: {
    gradient: 'from-rose-50 to-pink-50 dark:from-rose-900/20 dark:to-pink-900/10',
    border: 'border-rose-200/50 dark:border-rose-800/50',
    hoverBorder: 'hover:border-rose-400 dark:hover:border-rose-500',
    shadow: 'hover:shadow-rose-200/50 dark:hover:shadow-rose-500/30',
    icon: 'text-rose-600 dark:text-rose-400',
    arrow: 'group-hover:text-rose-600 dark:group-hover:text-rose-400',
  },
  cyan: {
    gradient: 'from-cyan-50 to-sky-50 dark:from-cyan-900/20 dark:to-sky-900/10',
    border: 'border-cyan-200/50 dark:border-cyan-800/50',
    hoverBorder: 'hover:border-cyan-400 dark:hover:border-cyan-500',
    shadow: 'hover:shadow-cyan-200/50 dark:hover:shadow-cyan-500/30',
    icon: 'text-cyan-600 dark:text-cyan-400',
    arrow: 'group-hover:text-cyan-600 dark:group-hover:text-cyan-400',
  },
  emerald: {
    gradient: 'from-emerald-50 to-teal-50 dark:from-emerald-900/20 dark:to-teal-900/10',
    border: 'border-emerald-200/50 dark:border-emerald-800/50',
    hoverBorder: 'hover:border-emerald-400 dark:hover:border-emerald-500',
    shadow: 'hover:shadow-emerald-200/50 dark:hover:shadow-emerald-500/30',
    icon: 'text-emerald-600 dark:text-emerald-400',
    arrow: 'group-hover:text-emerald-600 dark:group-hover:text-emerald-400',
  },
  amber: {
    gradient: 'from-amber-50 to-yellow-50 dark:from-amber-900/20 dark:to-yellow-900/10',
    border: 'border-amber-200/50 dark:border-amber-800/50',
    hoverBorder: 'hover:border-amber-400 dark:hover:border-amber-500',
    shadow: 'hover:shadow-amber-200/50 dark:hover:shadow-amber-500/30',
    icon: 'text-amber-600 dark:text-amber-400',
    arrow: 'group-hover:text-amber-600 dark:group-hover:text-amber-400',
  },
  blue: {
    gradient: 'from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/10',
    border: 'border-blue-200/50 dark:border-blue-800/50',
    hoverBorder: 'hover:border-blue-400 dark:hover:border-blue-500',
    shadow: 'hover:shadow-blue-200/50 dark:hover:shadow-blue-500/30',
    icon: 'text-blue-600 dark:text-blue-400',
    arrow: 'group-hover:text-blue-600 dark:group-hover:text-blue-400',
  },
  indigo: {
    gradient: 'from-indigo-50 to-violet-50 dark:from-indigo-900/20 dark:to-violet-900/10',
    border: 'border-indigo-200/50 dark:border-indigo-800/50',
    hoverBorder: 'hover:border-indigo-400 dark:hover:border-indigo-500',
    shadow: 'hover:shadow-indigo-200/50 dark:hover:shadow-indigo-500/30',
    icon: 'text-indigo-600 dark:text-indigo-400',
    arrow: 'group-hover:text-indigo-600 dark:group-hover:text-indigo-400',
  },
  pink: {
    gradient: 'from-pink-50 to-rose-50 dark:from-pink-900/20 dark:to-rose-900/10',
    border: 'border-pink-200/50 dark:border-pink-800/50',
    hoverBorder: 'hover:border-pink-400 dark:hover:border-pink-500',
    shadow: 'hover:shadow-pink-200/50 dark:hover:shadow-pink-500/30',
    icon: 'text-pink-600 dark:text-pink-400',
    arrow: 'group-hover:text-pink-600 dark:group-hover:text-pink-400',
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
export function getPlaybookColors(
  colorName: string
): (typeof COLOR_MAP)[keyof typeof COLOR_MAP] {
  return COLOR_MAP[colorName] || COLOR_MAP[DEFAULT_COLOR];
}
