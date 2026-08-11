import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
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
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {name}
      </span>
      <span style={{ flexShrink: 0 }}>{value}</span>
    </div>
  );
}

type Tip = { lines: string[]; left: number; top: number };
type ShowTip = (lines: string[], target: HTMLElement) => void;

const TIP_W = 200;
const TIP_LINE_H = 17;
const TIP_CHROME = 14;
const EDGE = 4;

function useTip(): [Tip | null, ShowTip, () => void] {
  const [tip, setTip] = useState<Tip | null>(null);
  const show: ShowTip = (lines, target) => {
    const r = target.getBoundingClientRect();
    const h = lines.length * TIP_LINE_H + TIP_CHROME;
    const below = r.bottom + EDGE;
    setTip({
      lines,
      left: Math.max(EDGE, Math.min(r.left, window.innerWidth - TIP_W - EDGE)),
      top: below + h > window.innerHeight - EDGE ? Math.max(EDGE, r.top - h - EDGE) : below,
    });
  };
  return [tip, show, () => setTip(null)];
}

/**
 * Portalled to the body: the sticky cells set a z-index and so open their own
 * stacking contexts, which would otherwise paint over the tip.
 */
function TipLayer({ tip }: { tip: Tip }) {
  return createPortal(
    <div
      style={{
        position: 'fixed',
        left: tip.left,
        top: tip.top,
        maxWidth: TIP_W,
        padding: '0.3rem 0.45rem',
        background: 'var(--rv-surface)',
        border: '1px solid var(--rv-border)',
        borderRadius: 4,
        boxShadow: '0 4px 12px rgba(0,0,0,0.18)',
        fontSize: '0.7rem',
        fontWeight: 400,
        lineHeight: 1.4,
        color: 'var(--rv-fg)',
        whiteSpace: 'nowrap',
        pointerEvents: 'none',
        zIndex: 30,
      }}
    >
      {tip.lines.map((line, i) => (
        <div key={i} style={i === 0 ? { color: 'var(--rv-muted)' } : undefined}>
          {line}
        </div>
      ))}
    </div>,
    document.body,
  );
}

function Bars({
  buckets,
  bucketLabels,
  max,
  heading,
  onShow,
  onHide,
}: {
  buckets: number[];
  bucketLabels: string[];
  max: number;
  heading: string;
  onShow: ShowTip;
  onHide: () => void;
}) {
  return (
    <div
      style={{ display: 'flex', alignItems: 'flex-end', gap: 1, height: BAR_H, width: '100%' }}
      onMouseLeave={onHide}
    >
      {buckets.map((count, i) => (
        <div
          key={i}
          onMouseEnter={(e) =>
            onShow(
              [heading, bucketLabels[i], `${num(count)} ${count === 1 ? 'row' : 'rows'}`],
              e.currentTarget,
            )
          }
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

function ProfileCell({
  profile,
  heading,
  onShow,
  onHide,
}: {
  profile: Profile;
  heading: string;
  onShow: ShowTip;
  onHide: () => void;
}) {
  if (profile.kind === 'none') return null;

  if (profile.kind === 'identifier') {
    return <Chip text={`${num(profile.distinct)} uniq`} />;
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
    const { buckets, bucketLabels, max, min, hi, median, nonNull, total } = profile;
    if (total < SMALL_N) return <RangeLabel low={num(min)} high={num(hi)} />;
    const head = `${heading} · median ${num(median)} · ${num(nonNull)} of ${num(total)} set`;
    return (
      <>
        <Bars
          buckets={buckets}
          bucketLabels={bucketLabels}
          max={max}
          heading={head}
          onShow={onShow}
          onHide={onHide}
        />
        <RangeLabel low={num(min)} high={num(hi)} />
      </>
    );
  }

  const { buckets, bucketLabels, max, first, last, nonNull, total } = profile;
  if (total < SMALL_N) return <RangeLabel low={first.slice(0, 4)} high={last.slice(0, 4)} />;
  const head = `${heading} · ${num(nonNull)} of ${num(total)} set`;
  return (
    <>
      <Bars
        buckets={buckets}
        bucketLabels={bucketLabels}
        max={max}
        heading={head}
        onShow={onShow}
        onHide={onHide}
      />
      <RangeLabel low={first.slice(0, 4)} high={last.slice(0, 4)} />
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
  const [tip, showTip, hideTip] = useTip();

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
      {columns.map((col, i) => {
        const profile = profileFor(col);
        const heading = String(col.columnDef.header ?? col.id);
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
              <ProfileCell profile={profile} heading={heading} onShow={showTip} onHide={hideTip} />
            </div>
            {/* A <tr> takes only cells, so the single layer lives in the first. */}
            {i === 0 && tip && <TipLayer tip={tip} />}
          </td>
        );
      })}
    </tr>
  );
}
