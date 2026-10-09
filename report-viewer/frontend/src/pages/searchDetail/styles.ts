import type { CSSProperties } from 'react';

export const ROW_ACTIVE_BG = 'var(--rv-accent-soft)';
export const DETAIL_ZONE_BG = 'var(--rv-bg)';

// Sized for the header strip, whose text is 0.7rem.
export const compactBtn: CSSProperties = {
  fontSize: '0.65rem',
  padding: '0.1rem 0.35rem',
  border: '1px solid var(--rv-border)',
  background: 'var(--rv-surface)',
  color: 'var(--rv-fg)',
  borderRadius: 3,
  whiteSpace: 'nowrap',
  marginLeft: '0.6rem',
};

export const paginationBtn: CSSProperties = {
  fontSize: '0.72rem',
  // Fixes the line box height to a plain multiple of font-size - the
  // browser's own default ('normal') derives from the font's internal
  // ascent/descent metrics, which differ per glyph/font-fallback (e.g. the
  // "▾" in "Columns ▾" isn't necessarily covered by the same font as plain
  // Latin text), making otherwise-identical buttons a subpixel or more
  // taller than their plain-text siblings.
  lineHeight: 1,
  // Explicit, content-independent height - without it, an icon button
  // (e.g. the expand/contract toggle, whose SVG is taller than a text
  // line) still ends up taller than its text-only siblings even with
  // lineHeight fixed above. calc() keeps this in sync with fontSize/
  // padding/border above instead of hardcoding a px value that would
  // silently drift out of sync with them.
  height: 'calc(1em + 0.4rem + 2px)',
  padding: '0.2rem 0.45rem',
  border: '1px solid var(--rv-border)',
  background: 'var(--rv-surface)',
  color: 'var(--rv-fg)',
  borderRadius: 3,
  whiteSpace: 'nowrap',
};
