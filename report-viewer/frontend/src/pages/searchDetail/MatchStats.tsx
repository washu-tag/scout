import { useMemo } from 'react';
import { evidenceStats, type Tally } from './evidenceStats';

const SOURCE_LABEL: Record<string, string> = {
  text: 'Report text',
  diagnosis_code: 'Diagnosis code',
  unknown: 'Unknown',
};

const TOP_N = 8;

const muted: React.CSSProperties = { color: 'var(--rv-muted)', fontSize: '0.78rem' };

const num: React.CSSProperties = { fontVariantNumeric: 'tabular-nums' };

function Bar(props: { rows: Tally[]; total: number; label: (s: string) => string }) {
  return (
    <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.78rem' }}>
      <tbody>
        {props.rows.map((r) => (
          <tr key={r.label}>
            <td style={{ padding: '1px 0.5rem 1px 0', whiteSpace: 'nowrap' }}>
              {props.label(r.label)}
            </td>
            <td style={{ padding: '1px 0', width: '100%' }}>
              <span
                style={{
                  display: 'inline-block',
                  height: 8,
                  borderRadius: 2,
                  background: 'var(--rv-accent)',
                  width: `${props.total === 0 ? 0 : (r.count / props.total) * 100}%`,
                  minWidth: r.count > 0 ? 2 : 0,
                  verticalAlign: 'middle',
                }}
              />
            </td>
            <td
              style={{
                padding: '1px 0 1px 0.5rem',
                textAlign: 'right',
                fontVariantNumeric: 'tabular-nums',
                whiteSpace: 'nowrap',
                ...muted,
              }}
            >
              {r.count} ({props.total === 0 ? 0 : Math.round((r.count / props.total) * 100)}%)
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Phrases(props: { title: string; rows: Tally[]; distinct: number }) {
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
                {r.label}
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
export function MatchStats(props: { rows: Record<string, unknown>[] }) {
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

      <Bar rows={s.sources} total={s.total} label={(k) => SOURCE_LABEL[k] ?? k} />

      {s.crosstab.length > 0 && (
        <table
          style={{
            borderCollapse: 'collapse',
            marginTop: '0.6rem',
            fontSize: '0.76rem',
          }}
        >
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
            {s.crosstab.map((r) => (
              <tr key={r.source}>
                <td style={{ padding: '1px 0.6rem 1px 0', whiteSpace: 'nowrap' }}>
                  {SOURCE_LABEL[r.source] ?? r.source}
                </td>
                <td style={{ padding: '1px 0.6rem', textAlign: 'right', ...num }}>
                  {r.clean.toLocaleString()}
                </td>
                <td
                  // Admitted by a code while its own report text disagrees.
                  style={{
                    padding: '1px 0.6rem',
                    textAlign: 'right',
                    ...num,
                    ...(r.source === 'diagnosis_code' && r.negative > 0
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
      )}

      <Phrases title="Positive evidence" rows={s.positiveSpans} distinct={s.distinctPositive} />
      <Phrases title="Negative evidence" rows={s.negativeSpans} distinct={s.distinctNegative} />
    </div>
  );
}
