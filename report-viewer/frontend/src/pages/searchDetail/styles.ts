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
  padding: '0.2rem 0.45rem',
  border: '1px solid var(--rv-border)',
  background: 'var(--rv-surface)',
  color: 'var(--rv-fg)',
  borderRadius: 3,
  whiteSpace: 'nowrap',
};
