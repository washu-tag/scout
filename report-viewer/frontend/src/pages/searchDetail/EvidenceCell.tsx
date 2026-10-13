import { NegationIcon } from './icons';

// Chips share their colour with the marks they produce in the open report.
type Kind = 'text' | 'code' | 'negative';

const chip: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 3,
  flex: '0 1 auto',
  minWidth: 0,
  padding: '0 5px',
  borderRadius: 3,
  fontSize: '0.7rem',
  lineHeight: '1.4',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  border: '1px solid',
};

// Text and a diagnosis are both reasons the row qualifies, so one colour.
const POSITIVE: React.CSSProperties = {
  background: 'var(--rv-ev-positive-soft)',
  borderColor: 'var(--rv-ev-positive)',
  color: 'var(--rv-ev-positive)',
};

// Glyph only on the negation, so the state that matters is not colour-only.
const KIND: Record<Kind, { style: React.CSSProperties; icon?: () => React.ReactElement }> = {
  text: { style: POSITIVE },
  code: { style: POSITIVE },
  negative: {
    style: {
      // Clips first so the matched phrase and codes stay whole.
      flexShrink: 1000,
      minWidth: '4em',
      background: 'var(--rv-danger-soft)',
      borderColor: 'var(--rv-danger)',
      color: 'var(--rv-danger)',
    },
    icon: NegationIcon,
  },
};

function Chip(props: { kind: Kind; text: string; title: string }) {
  const { style, icon: Icon } = KIND[props.kind];
  return (
    <span title={props.title} style={{ ...chip, ...style }}>
      {Icon ? <Icon /> : null}
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{props.text}</span>
    </span>
  );
}

export function EvidenceCell(props: { row: Record<string, unknown> }) {
  const str = (k: string) => String(props.row[k] ?? '').trim();
  const positive = str('ev_positive_span');
  const negative = str('ev_negative_span');
  const codes = str('ev_dx_codes');
  const codeText = str('ev_dx_text');

  // The cell clips from the right and this order is the sort key, so the
  // leftmost chip is both the loudest and the one the column groups by.
  const chips: Array<{ kind: Kind; text: string; title: string }> = [];
  if (negative) {
    chips.push({ kind: 'negative', text: negative, title: negative });
  }
  if (positive) {
    chips.push({ kind: 'text', text: positive, title: positive });
  }
  // Shown whenever codes matched, not only when they admitted the row.
  if (codes) {
    chips.push({
      kind: 'code',
      text: codes,
      title: [codes, codeText].filter(Boolean).join(' - '),
    });
  }

  if (chips.length === 0) {
    return (
      <span
        style={{ color: 'var(--rv-muted)' }}
        title="Matched a predicate the viewer could not identify"
      >
        unexplained
      </span>
    );
  }

  return (
    <span style={{ display: 'flex', gap: 4, width: '100%', alignItems: 'center' }}>
      {chips.map((c, i) => (
        <Chip key={i} {...c} />
      ))}
    </span>
  );
}
