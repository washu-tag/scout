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
