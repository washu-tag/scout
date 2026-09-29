import { NegationIcon } from './icons';

// Why a row is in the cohort, as chips. Each chip shares its colour with the
// mark it produces in the report, so a yellow chip means yellow marks on open.
type Kind = 'text' | 'code' | 'negative';

const chip: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 3,
  maxWidth: '100%',
  padding: '0 5px',
  borderRadius: 3,
  fontSize: '0.7rem',
  lineHeight: '1.4',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  border: '1px solid',
};

// Text and a code are both reasons the row qualifies, so they share a colour
// and the glyph tells them apart. Red is only for text that contradicts.
const POSITIVE: React.CSSProperties = {
  background: 'var(--rv-ev-positive-soft)',
  borderColor: 'var(--rv-ev-positive)',
  color: 'var(--rv-ev-positive)',
};

// Only the negation carries a glyph. A code and a phrase already look
// nothing alike, and red plus a triangle double-encodes the one state a
// reviewer must not miss.
const KIND: Record<Kind, { style: React.CSSProperties; icon?: () => React.ReactElement }> = {
  text: { style: POSITIVE },
  code: { style: POSITIVE },
  negative: {
    style: {
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

const MAX_CHIPS = 2;

export function EvidenceCell(props: { row: Record<string, unknown> }) {
  const str = (k: string) => String(props.row[k] ?? '').trim();
  const positive = str('ev_positive_span');
  const negative = str('ev_negative_span');
  const codes = str('ev_dx_codes');
  const source = str('ev_source');

  const chips: Array<{ kind: Kind; text: string; title: string }> = [];
  // The code is the evidence when no text arm admitted the row, so it leads.
  if (source === 'diagnosis_code' && codes) {
    chips.push({ kind: 'code', text: codes, title: `Admitted by diagnosis code ${codes}` });
  }
  if (positive) {
    chips.push({ kind: 'text', text: positive, title: `Report text matched: ${positive}` });
  }
  if (negative) {
    chips.push({
      kind: 'negative',
      text: negative,
      title: `The report also rules this out: ${negative}`,
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

  const shown = chips.slice(0, MAX_CHIPS);
  const hidden = chips.length - shown.length;
  return (
    <span style={{ display: 'inline-flex', gap: 4, maxWidth: '100%', alignItems: 'center' }}>
      {shown.map((c, i) => (
        <Chip key={i} {...c} />
      ))}
      {hidden > 0 && (
        <span style={{ color: 'var(--rv-muted)', fontSize: '0.7rem', whiteSpace: 'nowrap' }}>
          +{hidden}
        </span>
      )}
    </span>
  );
}
