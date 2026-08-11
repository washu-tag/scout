import { useMemo } from 'react';
import type { Column } from '@tanstack/react-table';
import { profileColumn, type Profile, type Segment } from './columnStats';

type Row = Record<string, unknown>;

// Share order, not identity: one hue stepped light to dark, so a filter that
// changes the ranking cannot repaint segments.
const RAMP = [
  'color-mix(in oklab, var(--rv-accent) 100%, var(--rv-surface))',
  'color-mix(in oklab, var(--rv-accent) 55%, var(--rv-surface))',
  'color-mix(in oklab, var(--rv-accent) 28%, var(--rv-surface))',
];
const EMPTY_FILL = 'var(--rv-border)';

const BAR_H = 15;
const GAP = 2;
// One bucket per ~6px so bars never go sub-pixel.
const PX_PER_BUCKET = 6;
// Below this, percentages mislead and a distribution is noise.
const SMALL_N = 20;

const labelStyle: React.CSSProperties = {
  fontSize: '0.6rem',
  lineHeight: 1.25,
  color: 'var(--rv-muted)',
  fontWeight: 400,
};

const num = (n: number) => n.toLocaleString();
const pct = (n: number) => `${n < 1 && n > 0 ? '<1' : Math.round(n)}%`;

function Label({ name, value }: { name: string; value: string }) {
  return (
    <div style={{ ...labelStyle, display: 'flex', gap: 4 }}>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {name}
      </span>
      <span style={{ flexShrink: 0 }}>{value}</span>
    </div>
  );
}

function Bars({ buckets, max }: { buckets: number[]; max: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 1, height: BAR_H, width: '100%' }}>
      {buckets.map((count, i) => (
        <div
          key={i}
          style={{
            flex: 1,
            height: `${max === 0 ? 0 : Math.max(count === 0 ? 1 : 8, (count / max) * 100)}%`,
            minHeight: 1,
            background: count === 0 ? EMPTY_FILL : RAMP[0],
            borderRadius: '2px 2px 0 0',
          }}
        />
      ))}
    </div>
  );
}

function StackedBar({ parts }: { parts: Array<Segment & { fill: string }> }) {
  return (
    <div style={{ display: 'flex', gap: GAP, height: BAR_H, width: '100%', alignItems: 'stretch' }}>
      {parts.map((p) => (
        <div
          key={p.label}
          style={{ flex: `${Math.max(p.pct, 1.5)} 0 0`, background: p.fill, borderRadius: 2 }}
        />
      ))}
    </div>
  );
}

function RangeLabel({ low, high }: { low: string; high: string }) {
  if (low === high) return <div style={{ ...labelStyle, textAlign: 'center' }}>{low}</div>;
  return <Label name={low} value={high} />;
}

function Chip({ text }: { text: string }) {
  return (
    <span
      style={{
        display: 'inline-block',
        maxWidth: '100%',
        padding: '1px 6px',
        borderRadius: 10,
        background: 'var(--rv-surface)',
        border: '1px solid var(--rv-border)',
        fontSize: '0.7rem',
        color: 'var(--rv-fg)',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}
    >
      {text}
    </span>
  );
}

function ProfileCell({ profile }: { profile: Profile }) {
  if (profile.kind === 'none') return null;

  if (profile.kind === 'identifier') {
    // "unique", not "patients": these columns count distinct identifier values,
    // and the four patient identifiers disagree, so naming them patients would
    // assert several patient counts for one cohort.
    return (
      <>
        <Chip text={num(profile.distinct)} />
        <div style={labelStyle}>unique</div>
      </>
    );
  }

  if (profile.kind === 'categorical') {
    const { segments, other, empty, distinct, total } = profile;
    const rolledUp = distinct - segments.length;
    const parts = [
      ...segments.map((s, i) => ({ ...s, fill: RAMP[Math.min(i, RAMP.length - 1)] })),
      ...(other ? [{ ...other, label: `+${num(rolledUp)} more`, fill: RAMP[2] }] : []),
      ...(empty ? [{ ...empty, fill: EMPTY_FILL }] : []),
    ].filter((p) => p.count > 0);
    const small = total < SMALL_N;
    return (
      <>
        <StackedBar parts={parts} />
        {parts.map((p) => (
          <Label key={p.label} name={p.label} value={small ? num(p.count) : pct(p.pct)} />
        ))}
      </>
    );
  }

  if (profile.kind === 'numeric') {
    const { buckets, max, min, hi, total } = profile;
    const range = <RangeLabel low={num(min)} high={num(hi)} />;
    if (total < SMALL_N) return range;
    return (
      <>
        <Bars buckets={buckets} max={max} />
        {range}
      </>
    );
  }

  const { buckets, max, first, last, total } = profile;
  const range = <RangeLabel low={first.slice(0, 4)} high={last.slice(0, 4)} />;
  if (total < SMALL_N) return range;
  return (
    <>
      <Bars buckets={buckets} max={max} />
      {range}
    </>
  );
}

export function ColumnProfileRow({
  columns,
  rows,
  dateFields,
  stickyTop,
}: {
  columns: Column<Row, unknown>[];
  rows: Row[];
  dateFields: ReadonlySet<string>;
  stickyTop: number;
}) {
  // getVisibleLeafColumns() is a new array every render, so a useMemo on it
  // would recompute every sort and page click. Cache per column instead.
  const cache = useMemo(() => new Map<string, Profile>(), [rows, dateFields]);
  const profileFor = (col: Column<Row, unknown>): Profile => {
    const buckets = Math.max(4, Math.min(12, Math.floor(col.getSize() / PX_PER_BUCKET)));
    const key = `${col.id}:${buckets}`;
    let profile = cache.get(key);
    if (!profile) {
      profile = profileColumn(col.id, rows, dateFields.has(col.id), buckets);
      cache.set(key, profile);
    }
    return profile;
  };

  return (
    <tr>
      {columns.map((col) => {
        const profile = profileFor(col);
        return (
          <td
            key={col.id}
            style={{
              padding: '3px 0.45rem 4px',
              verticalAlign: profile.kind === 'identifier' ? 'middle' : 'bottom',
              background: 'var(--rv-surface-2)',
              boxShadow: 'inset 0 -1px 0 var(--rv-border)',
              position: 'sticky',
              top: stickyTop,
              zIndex: 1,
            }}
          >
            <div style={{ minWidth: 0 }}>
              <ProfileCell profile={profile} />
            </div>
          </td>
        );
      })}
    </tr>
  );
}
