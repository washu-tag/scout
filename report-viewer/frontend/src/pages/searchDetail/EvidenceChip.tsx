/** Which source admitted this row, and whether an exclusion pattern also hit. */

const LABEL: Record<string, string> = {
  text: 'Report text',
  diagnosis_code: 'DX code',
};

const base: React.CSSProperties = {
  display: 'inline-block',
  maxWidth: '100%',
  padding: '0 5px',
  borderRadius: 3,
  fontSize: '0.7rem',
  lineHeight: '1.35',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  border: '1px solid transparent',
};

export function EvidenceChip(props: { source: unknown; negativeSpan: unknown }) {
  const source = props.source == null ? '' : String(props.source);
  const label = source ? (LABEL[source] ?? source) : 'Unknown';
  const excluded = props.negativeSpan != null && String(props.negativeSpan) !== '';

  const style = excluded
    ? {
        ...base,
        background: 'var(--rv-accent-soft)',
        borderColor: 'var(--rv-danger)',
        color: 'var(--rv-danger)',
      }
    : !source
      ? { ...base, background: 'var(--rv-surface-2)', color: 'var(--rv-muted)' }
      : source === 'diagnosis_code'
        ? { ...base, background: 'var(--rv-surface-2)', color: 'var(--rv-muted)' }
        : { ...base, background: 'var(--rv-accent-soft)', color: 'var(--rv-fg)' };

  const title = excluded
    ? 'The report text also carries negative evidence - worth reviewing'
    : source
      ? `Matched on ${label.toLowerCase()}`
      : 'Matched a predicate the viewer could not identify';

  return (
    <span title={title} style={style}>
      {excluded ? `\u26a0 ${label}` : label}
    </span>
  );
}
