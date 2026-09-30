import { useMemo } from 'react';
import type { EvCategory, FilterState } from '../../api/client';
import { evidenceStats, type Tally } from './evidenceStats';

const CATEGORY_LABEL: Record<string, string> = {
  text_and_code: 'Report text and code',
  text: 'Report text only',
  diagnosis_code: 'Diagnosis code only',
  unknown: 'Unexplained',
};

const TOP_N = 8;

const muted: React.CSSProperties = { color: 'var(--rv-muted)', fontSize: '0.78rem' };

const num: React.CSSProperties = { fontVariantNumeric: 'tabular-nums' };

const linkish: React.CSSProperties = {
  border: 'none',
  background: 'transparent',
  padding: 0,
  font: 'inherit',
  color: 'var(--rv-accent)',
  cursor: 'pointer',
  textAlign: 'left',
};

function Phrases(props: {
  title: string;
  rows: Tally[];
  distinct: number;
  onPick?: (label: string) => void;
}) {
  if (props.rows.length === 0) return null;
  return (
    <div style={{ marginTop: '0.6rem' }}>
      <div style={{ ...muted, marginBottom: '0.2rem' }}>
        {props.title} - {props.distinct} distinct
      </div>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.76rem' }}>
        <tbody>
          {props.rows.slice(0, TOP_N).map((r) => (
            <tr key={r.label}>
              <td
                style={{
                  padding: '1px 0.5rem 1px 0',
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                }}
              >
                {props.onPick ? (
                  <button
                    type="button"
                    style={linkish}
                    title={`Filter the table to this phrase`}
                    onClick={() => props.onPick?.(r.label)}
                  >
                    {r.label}
                  </button>
                ) : (
                  r.label
                )}
              </td>
              <td
                style={{
                  padding: '1px 0',
                  textAlign: 'right',
                  fontVariantNumeric: 'tabular-nums',
                  whiteSpace: 'nowrap',
                  ...muted,
                }}
              >
                {r.count}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What this search matched, tallied from the cohort's own evidence columns. */
export function MatchStats(props: {
  rows: Record<string, unknown>[];
  onFilter?: (patch: Partial<FilterState>) => void;
}) {
  const s = useMemo(() => evidenceStats(props.rows), [props.rows]);

  return (
    <div style={{ marginTop: '1rem' }}>
      <div style={{ fontWeight: 600, marginBottom: '0.2rem', fontSize: '0.85rem' }}>
        What this search matched
      </div>
      <p style={{ margin: '0 0 0.5rem', lineHeight: 1.4, ...muted }}>
        Counted from the {s.total.toLocaleString()} loaded rows, using the search SQL&apos;s own
        predicates. Compare between searches to see what a reworded question changed.
      </p>

      <table style={{ borderCollapse: 'collapse', fontSize: '0.78rem' }}>
        <thead>
          <tr style={muted}>
            <th />
            <th style={{ padding: '0 0.6rem', fontWeight: 500, textAlign: 'right' }}>clean</th>
            <th style={{ padding: '0 0.6rem', fontWeight: 500, textAlign: 'right' }}>
              has negative
            </th>
          </tr>
        </thead>
        <tbody>
          {s.breakdown.map((r) => (
            <tr key={r.category}>
              <td style={{ padding: '1px 0.6rem 1px 0', whiteSpace: 'nowrap' }}>
                {props.onFilter ? (
                  <button
                    type="button"
                    style={linkish}
                    title="Filter the table to these rows"
                    onClick={() => props.onFilter?.({ ev_source: [r.category as EvCategory] })}
                  >
                    {CATEGORY_LABEL[r.category] ?? r.category}
                  </button>
                ) : (
                  (CATEGORY_LABEL[r.category] ?? r.category)
                )}
              </td>
              <td style={{ padding: '1px 0.6rem', textAlign: 'right', ...num }}>
                {r.clean.toLocaleString()}
              </td>
              <td
                // A code admitted the row while its own report text disagrees.
                style={{
                  padding: '1px 0.6rem',
                  textAlign: 'right',
                  ...num,
                  ...(r.category === 'diagnosis_code' && r.negative > 0
                    ? { color: 'var(--rv-danger)', fontWeight: 600 }
                    : {}),
                }}
              >
                {r.negative.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Phrases
        title="Positive evidence"
        rows={s.positiveSpans}
        distinct={s.distinctPositive}
        onPick={props.onFilter && ((label) => props.onFilter?.({ ev_positive_span: label }))}
      />
      <Phrases
        title="Negative evidence"
        rows={s.negativeSpans}
        distinct={s.distinctNegative}
        onPick={props.onFilter && ((label) => props.onFilter?.({ ev_negative_span: label }))}
      />
    </div>
  );
}
