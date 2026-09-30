import type { FilterState } from '../../api/client';

type Applied = { key: string; label: string; clear: Partial<FilterState> };

function range(label: string, min?: string, max?: string): string | null {
  if (min && max) return `${label} ${min} to ${max}`;
  if (min) return `${label} from ${min}`;
  if (max) return `${label} to ${max}`;
  return null;
}

function describe(f: FilterState): Applied[] {
  const out: Applied[] = [];
  const age = range('age', f.patient_age?.min, f.patient_age?.max);
  if (age) out.push({ key: 'patient_age', label: age, clear: { patient_age: undefined } });
  const dt = range('date', f.message_dt?.min, f.message_dt?.max);
  if (dt) out.push({ key: 'message_dt', label: dt, clear: { message_dt: undefined } });
  if (f.sex?.length)
    out.push({ key: 'sex', label: `sex ${f.sex.join(', ')}`, clear: { sex: undefined } });
  if (f.modality?.length)
    out.push({
      key: 'modality',
      label: `modality ${f.modality.join(', ')}`,
      clear: { modality: undefined },
    });
  const text: Array<[keyof FilterState, string]> = [
    ['service_name', 'service'],
    ['epic_mrn', 'MRN'],
    ['patient_mpi', 'MPI'],
    ['accession_number', 'accession'],
    ['sending_facility', 'facility'],
  ];
  for (const [field, label] of text) {
    const v = f[field];
    if (typeof v === 'string' && v)
      out.push({ key: field, label: `${label}: ${v}`, clear: { [field]: undefined } });
  }
  if (f.ev_dx_codes?.length)
    out.push({
      key: 'ev_dx_codes',
      label: `dx ${f.ev_dx_codes.join(', ')}`,
      clear: { ev_dx_codes: undefined },
    });
  if (f.ev_source?.length)
    out.push({
      key: 'ev_source',
      label: `matched on ${f.ev_source.join(', ')}`,
      clear: { ev_source: undefined },
    });
  if (f.ev_has_negative !== undefined)
    out.push({
      key: 'ev_has_negative',
      label: f.ev_has_negative ? 'has negative' : 'no contradicting text',
      clear: { ev_has_negative: undefined },
    });
  if (f.ev_positive_span)
    out.push({
      key: 'ev_positive_span',
      label: `"${f.ev_positive_span}"`,
      clear: { ev_positive_span: undefined },
    });
  if (f.ev_negative_span)
    out.push({
      key: 'ev_negative_span',
      label: `negated "${f.ev_negative_span}"`,
      clear: { ev_negative_span: undefined },
    });
  return out;
}

/** Everything currently narrowing the table, so an active filter is never
 *  visible only as a count on the Filters button. */
export function AppliedFilterChips(props: {
  filters: FilterState;
  shown: number;
  total: number;
  onChange: (next: FilterState) => void;
}) {
  const applied = describe(props.filters);
  if (applied.length === 0) return null;

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.3rem',
        alignItems: 'center',
        marginTop: '0.35rem',
        fontSize: '0.7rem',
      }}
    >
      <span style={{ color: 'var(--rv-muted)', marginRight: '0.15rem' }}>Filters</span>
      {applied.map((a) => (
        <span
          key={a.key}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 3,
            padding: '1px 3px 1px 8px',
            borderRadius: 999,
            border: '1px solid var(--rv-accent)',
            background: 'var(--rv-accent-soft)',
            color: 'var(--rv-fg)',
            maxWidth: 260,
          }}
        >
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {a.label}
          </span>
          <button
            type="button"
            aria-label={`Remove filter ${a.label}`}
            onClick={() => props.onChange({ ...props.filters, ...a.clear })}
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--rv-muted)',
              cursor: 'pointer',
              padding: '0 3px',
              fontSize: '0.8rem',
              lineHeight: 1,
            }}
          >
            x
          </button>
        </span>
      ))}
      <span style={{ color: 'var(--rv-muted)', fontVariantNumeric: 'tabular-nums' }}>
        {props.shown.toLocaleString()} of {props.total.toLocaleString()} rows
      </span>
      <button
        type="button"
        onClick={() => props.onChange({})}
        style={{
          border: 'none',
          background: 'transparent',
          color: 'var(--rv-accent)',
          cursor: 'pointer',
          fontSize: '0.7rem',
          padding: '1px 4px',
        }}
      >
        clear all
      </button>
    </div>
  );
}
