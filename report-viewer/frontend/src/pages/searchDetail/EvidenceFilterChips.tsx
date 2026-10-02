import { useMemo } from 'react';
import { collapse, evidenceCategory, type EvCategory, type FilterState } from '../../api/client';
import { NegationIcon } from './icons';

// Pill-shaped, unlike the 3px evidence chips, so clickable never looks static.
const pill: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  padding: '1px 8px',
  borderRadius: 999,
  border: '1px solid',
  fontSize: '0.7rem',
  lineHeight: '1.5',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  maxWidth: 220,
};

const CATEGORY_LABEL: Record<EvCategory, string> = {
  text_and_code: 'text + diagnosis',
  text: 'report text',
  diagnosis_code: 'diagnosis',
  unknown: 'unexplained',
};

type Tone = 'positive' | 'negative' | 'neutral';

function tones(tone: Tone, active: boolean): React.CSSProperties {
  const fg = tone === 'negative' ? 'var(--rv-danger)' : 'var(--rv-ev-positive)';
  if (tone === 'neutral') {
    return active
      ? { background: 'var(--rv-accent)', borderColor: 'var(--rv-accent)', color: '#fff' }
      : {
          background: 'var(--rv-surface)',
          borderColor: 'var(--rv-border)',
          color: 'var(--rv-muted)',
        };
  }
  return active
    ? { background: fg, borderColor: fg, color: 'var(--rv-surface)' }
    : {
        background: 'var(--rv-surface)',
        borderColor: 'var(--rv-border)',
        color: 'var(--rv-muted)',
      };
}

function Pill(props: {
  label: string;
  count: number;
  tone: Tone;
  active: boolean;
  title: string;
  icon?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={props.active}
      title={props.title}
      onClick={props.onClick}
      style={{ ...pill, ...tones(props.tone, props.active) }}
    >
      {props.icon ? <NegationIcon /> : null}
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{props.label}</span>
      <span style={{ opacity: 0.75, fontVariantNumeric: 'tabular-nums' }}>
        {props.count.toLocaleString()}
      </span>
    </button>
  );
}

/** Counts are over the unfiltered cohort, so they hold still as you select. */
export function EvidenceFilterChips(props: {
  rows: Record<string, unknown>[];
  filters: FilterState;
  onChange: (next: FilterState) => void;
}) {
  const facets = useMemo(() => {
    const categories = new Map<EvCategory, number>();
    let negative = 0;
    for (const row of props.rows) {
      const c = evidenceCategory(row);
      categories.set(c, (categories.get(c) ?? 0) + 1);
      if (collapse(row['ev_negative_span'])) negative += 1;
    }
    return {
      categories: [...categories.entries()].sort((a, b) => b[1] - a[1]),
      negative,
    };
  }, [props.rows]);

  const f = props.filters;
  const patch = (next: Partial<FilterState>) => props.onChange({ ...f, ...next });

  const toggleCategory = (c: EvCategory) => {
    const cur = new Set(f.ev_source ?? []);
    if (cur.has(c)) cur.delete(c);
    else cur.add(c);
    const list = [...cur];
    patch({ ev_source: list.length > 0 ? list : undefined });
  };

  const selected = new Set(f.ev_source ?? []);
  const spanChips = [f.ev_positive_span, f.ev_negative_span];
  const anyActive = selected.size > 0 || f.ev_has_negative !== undefined || spanChips.some(Boolean);

  if (facets.categories.length === 0) return null;

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.3rem',
        alignItems: 'center',
        marginTop: '0.5rem',
      }}
    >
      <span style={{ color: 'var(--rv-muted)', fontSize: '0.7rem', marginRight: '0.15rem' }}>
        Matched on
      </span>

      {facets.categories.map(([category, count]) => (
        <Pill
          key={category}
          label={CATEGORY_LABEL[category]}
          count={count}
          tone={category === 'unknown' ? 'neutral' : 'positive'}
          active={selected.has(category)}
          title={`Show only rows the query admitted on ${CATEGORY_LABEL[category]}`}
          onClick={() => toggleCategory(category)}
        />
      ))}

      {facets.negative > 0 && (
        <Pill
          label={f.ev_has_negative === false ? 'no negated phrase' : 'negated phrase'}
          count={
            f.ev_has_negative === false ? props.rows.length - facets.negative : facets.negative
          }
          tone={f.ev_has_negative === false ? 'positive' : 'negative'}
          icon={f.ev_has_negative !== false}
          active={f.ev_has_negative !== undefined}
          title="Click to keep only these, again to exclude them, again to clear"
          onClick={() =>
            patch({
              ev_has_negative:
                f.ev_has_negative === undefined
                  ? true
                  : f.ev_has_negative === true
                    ? false
                    : undefined,
            })
          }
        />
      )}

      {/* Arrive by clicking a phrase in the stats panel; these are the way out. */}
      {f.ev_positive_span && (
        <Pill
          label={`"${f.ev_positive_span}"`}
          count={
            props.rows.filter(
              (r) =>
                collapse(r['ev_positive_span']).toLowerCase() === f.ev_positive_span?.toLowerCase(),
            ).length
          }
          tone="positive"
          active
          title={`Clear the phrase filter ${f.ev_positive_span}`}
          onClick={() => patch({ ev_positive_span: undefined })}
        />
      )}
      {f.ev_negative_span && (
        <Pill
          label={`"${f.ev_negative_span}"`}
          count={
            props.rows.filter(
              (r) =>
                collapse(r['ev_negative_span']).toLowerCase() === f.ev_negative_span?.toLowerCase(),
            ).length
          }
          tone="negative"
          icon
          active
          title={`Clear the phrase filter ${f.ev_negative_span}`}
          onClick={() => patch({ ev_negative_span: undefined })}
        />
      )}

      {anyActive && (
        <button
          type="button"
          onClick={() =>
            patch({
              ev_source: undefined,
              ev_has_negative: undefined,
              ev_positive_span: undefined,
              ev_negative_span: undefined,
            })
          }
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--rv-accent)',
            fontSize: '0.7rem',
            cursor: 'pointer',
            padding: '1px 4px',
          }}
        >
          clear
        </button>
      )}
    </div>
  );
}
