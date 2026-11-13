import type { RenderChip, RenderModel } from './types';

export interface ShellSection {
  id: string;
  title: string;
  icon: string;
  chips: RenderChip[];
}

export interface ChipTarget {
  href: string;
  // Navigates within the launchpad, keeping the rail and any open app frames.
  internal: boolean;
  newTab: boolean;
}

// The rail: every chip in page order, one section per group. Routes key on
// chip id, so when two sources ship the same id the first one in page order
// owns it.
export function shellSections(model: RenderModel): ShellSection[] {
  const seen = new Set<string>();
  const sections: ShellSection[] = [];
  for (const group of model.rows.flatMap((row) => row.groups)) {
    const chips = group.chips.filter((chip) => {
      if (seen.has(chip.id)) return false;
      seen.add(chip.id);
      return true;
    });
    if (chips.length > 0) {
      sections.push({ id: group.id, title: group.title, icon: group.icon, chips });
    }
  }
  return sections;
}

export function shellPath(id: string): string {
  return `/app/${id}`;
}

export function chipTarget(chip: RenderChip, embedApps: boolean): ChipTarget {
  if (chip.kind === 'service' && embedApps) {
    return { href: shellPath(chip.id), internal: true, newTab: false };
  }
  if (chip.kind === 'page') return { href: chip.href, internal: true, newTab: false };
  return { href: chip.href, internal: false, newTab: chip.newTab };
}

// A deep link's path (with query) on the chip's own origin. Anything that would
// leave that origin falls back to the chip's destination.
export function frameUrl(chip: RenderChip, path: string | null): string {
  if (!path || !path.startsWith('/') || path.startsWith('//')) return chip.href;
  try {
    const base = new URL(chip.href);
    const url = new URL(path, base);
    return url.origin === base.origin ? url.href : chip.href;
  } catch {
    return chip.href;
  }
}

// The chip's origin or one of its subdomains: JupyterHub serves each user's
// server at <user>.<hub host>.
export function isChipOrigin(chip: RenderChip, origin: string): boolean {
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return false;
  }
  const home = new URL(chip.href);
  return (
    url.protocol === home.protocol &&
    url.port === home.port &&
    (url.hostname === home.hostname || url.hostname.endsWith(`.${home.hostname}`))
  );
}

// The launchpad address for a location a framed app reports, or null when the
// location is off the chip's origin. Only the path travels: reopening it on the
// chip's own host, JupyterHub redirects to the user's server.
export function shellLocation(chip: RenderChip, href: unknown): string | null {
  if (typeof href !== 'string') return null;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  const home = new URL(chip.href);
  if (!isChipOrigin(chip, url.origin)) return null;
  if (url.href === home.href) return shellPath(chip.id);
  return `${shellPath(chip.id)}?path=${encodeURIComponent(url.pathname + url.search)}${url.hash}`;
}

// The framed app that owns a Scout service URL: among the chips on its host, the
// one whose own path is the longest prefix of the URL's path, else the first.
export function chipForUrl(
  sections: ShellSection[],
  url: URL,
  embedApps: boolean,
): RenderChip | undefined {
  if (!embedApps) return undefined;
  const candidates = sections
    .flatMap((section) => section.chips)
    .filter((chip) => chip.kind === 'service' && new URL(chip.href).host === url.host);
  let best: RenderChip | undefined;
  let bestLength = -1;
  for (const chip of candidates) {
    const prefix = new URL(chip.href).pathname.replace(/\/$/, '');
    const matches = url.pathname === prefix || url.pathname.startsWith(`${prefix}/`);
    if (matches && prefix.length > bestLength) {
      best = chip;
      bestLength = prefix.length;
    }
  }
  return best ?? candidates[0];
}

export function embeddedChip(
  sections: ShellSection[],
  id: string,
  embedApps: boolean,
): RenderChip | undefined {
  if (!embedApps) return undefined;
  return sections
    .flatMap((section) => section.chips)
    .find((chip) => chip.id === id && chip.kind === 'service');
}
