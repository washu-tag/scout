import type { ToneName } from './tones';

export type Audience = 'user' | 'admin';
export type GroupLayout = 'cards' | 'rows' | 'tiles';
export type GroupWidth = 'full' | 'half';

export interface ChipLink {
  subdomain?: string;
  path?: string;
  url?: string;
}

export interface FooterLink {
  text: string;
  url: string;
}

// A chip as validated and normalized from a catalog document (ADR 0034).
export interface Chip {
  id: string;
  title: string;
  description: string;
  icon: string;
  iconData?: string;
  tone: ToneName;
  link: ChipLink;
  newTab: boolean;
  sandbox: boolean;
  group: string;
  weight: number;
  audience: Audience;
  enabled: boolean;
  source: string;
}

export interface Group {
  id: string;
  title: string;
  description: string;
  icon: string;
  weight: number;
  layout: GroupLayout;
  maxColumns: number;
  width: GroupWidth;
  audience: Audience;
  railMenu?: string;
  footerLink?: FooterLink;
  source: string;
  // Lower ranks win when two documents define the same group id. The mounted
  // catalog directory is listed first in LAUNCHPAD_CATALOG_DIRS, so it ranks 0.
  sourceRank: number;
}

export interface Diagnostic {
  // Where the problem came from: "<dir>/<file>" for loaded documents, a
  // chip/group id where one exists.
  source: string;
  subject?: string;
  message: string;
}

// Everything parsed and validated, before any per-request concerns
// (audience, host) apply. This is what the loader snapshot holds.
export interface Catalog {
  chips: Chip[];
  groups: Group[];
  diagnostics: Diagnostic[];
}

// Where a chip leads: a Scout service on its own subdomain, a launchpad page,
// or an outside URL.
export type ChipKind = 'service' | 'page' | 'external';

// The client-serializable render model: names instead of components, hrefs
// instead of link descriptors, already audience-filtered and laid out.
export interface RenderChip {
  // Which document supplied the chip. Chip ids are unique only within their
  // source document, so render keys pair source with id.
  source: string;
  id: string;
  title: string;
  description: string;
  icon: string;
  iconData?: string;
  tone: ToneName;
  kind: ChipKind;
  href: string;
  newTab: boolean;
  sandbox: boolean;
}

export interface RenderGroup {
  id: string;
  title: string;
  description: string;
  icon: string;
  layout: GroupLayout;
  columns: number;
  width: GroupWidth;
  audience: Audience;
  railMenu?: string;
  chips: RenderChip[];
  footerLink?: FooterLink;
}

// One visual band of the page: a single full-width group, or two half-width
// groups side by side.
export interface RenderRow {
  groups: RenderGroup[];
}

export interface RenderModel {
  rows: RenderRow[];
  diagnostics: Diagnostic[];
}
