/** Which arm of the search SQL admitted this row. */

const LABEL: Record<string, string> = {
  impression: 'Impression',
  findings: 'Findings',
  addendum: 'Addendum',
  technician_note: 'Tech note',
  report_text: 'Full text',
  diagnosis_code: 'Code',
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

export function EvidenceChip(props: { source: unknown; contradicted: unknown }) {
  const source = props.source == null ? '' : String(props.source);
  if (!source) return <span style={{ color: 'var(--rv-muted)' }}>-</span>;

  const label = LABEL[source] ?? source;
  // Only a code-admitted row can be contradicted; a text match is its own evidence.
  const contradicted = props.contradicted === true && source === 'diagnosis_code';

  return (
    <span
      title={
        contradicted
          ? 'Matched a diagnosis code, but the report text rules the finding out'
          : `Matched on ${label.toLowerCase()}`
      }
      style={
        contradicted
          ? {
              ...base,
              background: 'var(--rv-accent-soft)',
              borderColor: 'var(--rv-danger)',
              color: 'var(--rv-danger)',
            }
          : source === 'diagnosis_code'
            ? { ...base, background: 'var(--rv-surface-2)', color: 'var(--rv-muted)' }
            : { ...base, background: 'var(--rv-accent-soft)', color: 'var(--rv-fg)' }
      }
    >
      {contradicted ? `⚠ ${label}` : label}
    </span>
  );
}
