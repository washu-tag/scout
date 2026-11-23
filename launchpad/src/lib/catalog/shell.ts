import type { Audience, RenderChip, RenderModel } from './types';

export interface ShellSection {
  id: string;
  title: string;
  icon: string;
  audience: Audience;
  railMenu?: string;
  chips: RenderChip[];
  // Sections whose chips the rail lists in this one's menu, ahead of its own.
  nested?: ShellSection[];
  // The section heading's link, when it opens a Scout service: routed like a chip, not listed.
  link?: RenderChip;
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
      const { id, title, icon, audience, railMenu } = group;
      const link = group.footerLink?.chip.kind === 'service' ? group.footerLink.chip : undefined;
      sections.push({ id, title, icon, audience, railMenu, chips, ...(link ? { link } : {}) });
    }
  }
  return sections;
}

// The rail lists Core Services' chips first, wherever group weights put that group on
// the page, then the other sections in page order, admin-only ones last. A section
// whose railMenu names another listed section goes in that section's menu instead,
// one level deep.
const RAIL_GROUP = 'core';

export function railSections(sections: ShellSection[]): {
  pinned?: ShellSection;
  rest: ShellSection[];
  admin: ShellSection[];
} {
  const pinned = sections.find((section) => section.id === RAIL_GROUP) ?? sections[0];
  const byId = new Map(sections.map((section) => [section.id, section]));
  const hostOf = (section: ShellSection) => {
    const host = section.railMenu ? byId.get(section.railMenu) : undefined;
    return section !== pinned && host && host !== section && host !== pinned && !host.railMenu
      ? host
      : undefined;
  };
  const others = sections
    .filter((section) => section !== pinned && !hostOf(section))
    .map((section) => {
      const nested = sections.filter((other) => hostOf(other) === section);
      return nested.length > 0 ? { ...section, nested } : section;
    });
  return {
    pinned,
    rest: others.filter((section) => section.audience !== 'admin'),
    admin: others.filter((section) => section.audience === 'admin'),
  };
}

// Every chip the shell can open, heading links included.
export function routableChips(sections: ShellSection[]): RenderChip[] {
  return sections.flatMap((section) =>
    section.link ? [...section.chips, section.link] : section.chips,
  );
}

// Whether `id` is a section heading's link. A heading link names a page ("See all"), so it
// opens that page each time; an app's own chip resumes where its frame was.
export function isHeadingLink(sections: ShellSection[], id: string): boolean {
  return sections.some((section) => section.link?.id === id);
}

export function isCurrentChip(chip: RenderChip, pathname: string): boolean {
  const target = chipTarget(chip);
  return target.internal && (pathname === target.href || pathname.startsWith(`${target.href}/`));
}

// The chip the launchpad page at `pathname` shows, heading links included.
export function currentChip(sections: ShellSection[], pathname: string): RenderChip | undefined {
  return routableChips(sections).find((chip) => isCurrentChip(chip, pathname));
}

export function shellPath(id: string): string {
  return `/app/${id}`;
}

export function chipTarget(chip: RenderChip): ChipTarget {
  if (chip.kind === 'service') {
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

// The page inside the app that a launchpad address /app/<id>/<path>?<query>#<hash>
// names, or null for the app's start page or another app's address.
export function deepPath(
  id: string,
  location: { pathname: string; search: string; hash: string },
): string | null {
  const prefix = shellPath(id);
  const { pathname, search, hash } = location;
  if (pathname !== prefix && !pathname.startsWith(`${prefix}/`)) return null;
  const rest = pathname.slice(prefix.length);
  return rest || search || hash ? `${rest || '/'}${search}${hash}` : null;
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
  return `${shellPath(chip.id)}${url.pathname}${url.search}${url.hash}`;
}

// The framed app that owns a Scout service URL: among the chips on its host (or,
// for JupyterHub's per-user servers, a host it is a subdomain of), the one whose
// own path is the longest prefix of the URL's path, else the first.
export function chipForUrl(sections: ShellSection[], url: URL): RenderChip | undefined {
  const services = routableChips(sections).filter((chip) => chip.kind === 'service');
  const sameHost = services.filter((chip) => new URL(chip.href).host === url.host);
  const candidates = sameHost.length
    ? sameHost
    : services.filter((chip) => isChipOrigin(chip, url.origin));
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

const DNS_LABEL = /^[a-z0-9-]{1,63}$/;

// `value` when it is a subdomain of the launchpad's host (port included), else null.
export function serviceHost(value: string, launchpadHost: string): string | null {
  const host = value.toLowerCase();
  const suffix = `.${launchpadHost.toLowerCase()}`;
  if (!host.endsWith(suffix)) return null;
  const labels = host.slice(0, -suffix.length).split('.');
  return labels.every((label) => DNS_LABEL.test(label)) ? host : null;
}

// The service URL an `/open/<service host>/<path>` request names, from the raw
// (still percent-encoded) request path, when its host is a subdomain of the
// launchpad's host; else null.
export function openTarget(
  pathname: string,
  search: string,
  launchpad: { protocol: string; host: string },
): URL | null {
  const prefix = '/open/';
  if (!pathname.startsWith(prefix)) return null;
  const segment = pathname.slice(prefix.length).split('/')[0];
  const host = serviceHost(segment, launchpad.host);
  if (!host) return null;
  const path = pathname.slice(prefix.length + segment.length) || '/';
  const target = new URL(`${launchpad.protocol}://${host}${path}${search}`);
  return target.host === host ? target : null;
}

function onHost(launchpad: URL, value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === launchpad.protocol &&
      url.port === launchpad.port &&
      (url.hostname === launchpad.hostname || url.hostname.endsWith(`.${launchpad.hostname}`))
    );
  } catch {
    return false;
  }
}

// Whether `origin` may send `scout:open`: the launchpad itself or a service on a
// single-label subdomain of its host. Deeper hosts (JupyterHub's per-user servers)
// serve users' own content, so they can't.
function canAskToOpen(launchpad: URL, origin: string): boolean {
  if (!onHost(launchpad, origin)) return false;
  const host = new URL(origin).hostname;
  if (host === launchpad.hostname) return true;
  return !host.slice(0, -launchpad.hostname.length - 1).includes('.');
}

// A `scout:open` request: the framed app and launchpad address for `href`, when the
// sender may ask (canAskToOpen), `href` is on the launchpad's host or a subdomain of
// it, and a chip owns `href`. A sandboxed chip's origin can't ask: its pages may run
// content the app didn't write.
export function openRequest(
  sections: ShellSection[],
  launchpad: URL,
  origin: string,
  href: unknown,
): { chip: RenderChip; address: string; href: string } | null {
  if (typeof href !== 'string' || !canAskToOpen(launchpad, origin) || !onHost(launchpad, href)) {
    return null;
  }
  const chips = routableChips(sections);
  if (chips.some((chip) => chip.sandbox && isChipOrigin(chip, origin))) return null;
  const url = new URL(href);
  const chip = chipForUrl(sections, url);
  const address = chip ? shellLocation(chip, url.href) : null;
  return chip && address ? { chip, address, href: url.href } : null;
}

// The most recently used app ids, newest last, at most `max`.
export function recentApps(recent: string[], id: string, max: number): string[] {
  return [...recent.filter((other) => other !== id), id].slice(-max);
}

export function embeddedChip(sections: ShellSection[], id: string): RenderChip | undefined {
  return routableChips(sections).find((chip) => chip.id === id && chip.kind === 'service');
}
