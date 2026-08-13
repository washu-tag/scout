import { DEFAULT_GROUP_ICON } from './icons';
import { defaultColumns } from './schema';
import type {
  Catalog,
  Chip,
  ChipLink,
  Diagnostic,
  Group,
  RenderGroup,
  RenderModel,
  RenderRow,
} from './types';

export interface Origin {
  protocol: 'http' | 'https';
  host: string;
}

export interface AssembleOptions {
  origin: Origin;
  isAdmin: boolean;
}

// Chip authors state a subdomain, never a hostname: links resolve against the
// request's own host (ADR 0034), which is exactly what the browser sees.
export function resolveHref(link: ChipLink, origin: Origin): string {
  if (link.url) return link.url;
  if (link.subdomain) {
    return `${origin.protocol}://${link.subdomain}.${origin.host}${link.path ?? ''}`;
  }
  return link.path ?? '/';
}

function byWeightTitleId(a: { weight: number; title: string; id: string }, b: typeof a): number {
  return a.weight - b.weight || a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
}

function titleCaseId(id: string): string {
  return id
    .split('-')
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ');
}

// Per-request assembly: filter the validated catalog by the viewer's
// audience, resolve links against the request origin, merge and order groups,
// and pack half-width groups into side-by-side rows. Pure computation over
// the loader snapshot — no I/O.
export function assemble(catalog: Catalog, options: AssembleOptions): RenderModel {
  const { origin, isAdmin } = options;
  const diagnostics: Diagnostic[] = [...catalog.diagnostics];
  const audienceOk = (audience: 'user' | 'admin') => audience === 'user' || isAdmin;

  // Group definitions merge across documents; the launchpad's own mounted
  // catalog (lowest source rank) wins, then lowest weight, then source name.
  const groupDefs = new Map<string, Group>();
  for (const candidate of [...catalog.groups].sort(
    (a, b) =>
      a.sourceRank - b.sourceRank || a.weight - b.weight || a.source.localeCompare(b.source),
  )) {
    const existing = groupDefs.get(candidate.id);
    if (existing) {
      diagnostics.push({
        source: candidate.source,
        subject: candidate.id,
        message: `group "${candidate.id}" already defined by ${existing.source}; this definition ignored`,
      });
      continue;
    }
    groupDefs.set(candidate.id, candidate);
  }

  const visibleChips = catalog.chips.filter((chip) => chip.enabled && audienceOk(chip.audience));

  const membership = new Map<string, Chip[]>();
  for (const chip of visibleChips) {
    if (!groupDefs.has(chip.group)) {
      // A chip must always land somewhere: synthesize a group from the id.
      groupDefs.set(chip.group, {
        id: chip.group,
        title: titleCaseId(chip.group),
        description: '',
        icon: DEFAULT_GROUP_ICON,
        weight: 500,
        layout: 'cards',
        maxColumns: defaultColumns('cards'),
        width: 'full',
        audience: 'user',
        source: chip.source,
        sourceRank: Number.MAX_SAFE_INTEGER,
      });
      diagnostics.push({
        source: chip.source,
        subject: chip.id,
        message: `chip references undefined group "${chip.group}"; a default group was synthesized`,
      });
    }
    const members = membership.get(chip.group) ?? [];
    members.push(chip);
    membership.set(chip.group, members);
  }

  // A user chip in an admin group
  for (const chip of catalog.chips) {
    if (!chip.enabled || chip.audience !== 'user') continue;
    if (groupDefs.get(chip.group)?.audience !== 'admin') continue;
    diagnostics.push({
      source: chip.source,
      subject: chip.id,
      message: `chip audience "user" is wider than group "${chip.group}" (audience "admin"); the chip renders for admins only`,
    });
  }

  const renderGroups: RenderGroup[] = [...groupDefs.values()]
    .filter((group) => audienceOk(group.audience) && (membership.get(group.id)?.length ?? 0) > 0)
    .sort(byWeightTitleId)
    .map((group) => {
      const chips = (membership.get(group.id) ?? []).sort(byWeightTitleId);
      return {
        id: group.id,
        title: group.title,
        description: group.description,
        icon: group.icon,
        layout: group.layout,
        columns: Math.max(1, Math.min(chips.length, group.maxColumns)),
        width: group.width,
        footerLink: group.footerLink,
        chips: chips.map((chip) => ({
          source: chip.source,
          id: chip.id,
          title: chip.title,
          description: chip.description,
          icon: chip.icon,
          iconData: chip.iconData,
          tone: chip.tone,
          href: resolveHref(chip.link, origin),
          newTab: chip.newTab,
        })),
      };
    });

  // Consecutive visible half-width groups pair side by side; an unpaired half
  // renders full. This reproduces the classic page exactly: Playbooks and
  // Admin Tools pair for admins, and Playbooks stands alone full-width for
  // everyone else.
  const rows: RenderRow[] = [];
  for (let i = 0; i < renderGroups.length; i += 1) {
    const group = renderGroups[i];
    const next = renderGroups[i + 1];
    if (group.width === 'half' && next?.width === 'half') {
      rows.push({ groups: [group, next] });
      i += 1;
    } else {
      rows.push({ groups: [group] });
    }
  }

  return {
    rows,
    // Diagnostics surface to admins only: the person who just shipped a broken
    // chip is looking at the page, not at Loki.
    diagnostics: isAdmin ? diagnostics : [],
  };
}
