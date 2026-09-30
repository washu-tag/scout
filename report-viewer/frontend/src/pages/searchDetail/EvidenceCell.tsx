import { NegationIcon } from './icons';

// Why a row is in the cohort, as one status marker. Quiet when the evidence
// is solid, loud for the two states a reviewer has to look at. The phrases
// themselves live in the review panel and the stats panel, both of which show
// them in context rather than truncated to a column.
type Mark = 'ok' | 'code' | 'negative' | 'unknown';

const marker: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '1.1rem',
  lineHeight: 1,
  fontSize: '0.8rem',
};

export function EvidenceCell(props: { row: Record<string, unknown> }) {
  const str = (k: string) => String(props.row[k] ?? '').trim();
  const positive = str('ev_positive_span');
  const negative = str('ev_negative_span');
  const codes = str('ev_dx_codes');
  const codeText = str('ev_dx_text');
  const source = str('ev_source');

  const mark: Mark = negative
    ? 'negative'
    : source === 'diagnosis_code'
      ? 'code'
      : source === 'text' || source === 'text_and_code'
        ? 'ok'
        : 'unknown';

  const dx = [codes, codeText].filter(Boolean).join(' - ');
  const title =
    mark === 'unknown'
      ? 'Matched a predicate the viewer could not identify'
      : [
          source === 'text_and_code'
            ? 'Report text and diagnosis code'
            : source === 'text'
              ? 'Report text'
              : 'Diagnosis code only',
          positive && `matched "${positive}"`,
          negative && `but the report also rules this out: "${negative}"`,
          dx,
        ]
          .filter(Boolean)
          .join(' - ');

  if (mark === 'negative') {
    return (
      <span title={title} style={{ ...marker, color: 'var(--rv-danger)' }}>
        <NegationIcon />
      </span>
    );
  }
  if (mark === 'unknown') {
    return (
      <span title={title} style={{ ...marker, color: 'var(--rv-muted)' }}>
        ?
      </span>
    );
  }
  // Hollow for a code with no text backing it: the same evidence, less of it.
  return (
    <span title={title} style={{ ...marker, color: 'var(--rv-ev-positive)' }}>
      {mark === 'code' ? '○' : '●'}
    </span>
  );
}
