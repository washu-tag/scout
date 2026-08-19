import { useMemo, useState } from 'react';
import type { Column } from '@tanstack/react-table';
import { profileColumn, type Profile, type Segment } from './columnStats';

type Row = Record<string, unknown>;

// One hue stepped by rank, not identity, so a filter that reorders segments
// doesn't repaint them. Steps live in theme.css since "more prominent" flips
// direction between light and dark mode.
const RAMP = ['var(--rv-profile-1)', 'var(--rv-profile-2)', 'var(--rv-profile-3)'];
// Aggregates, not values, so neither sits on the ramp.
const OTHER_FILL = 'var(--rv-profile-other)';
const EMPTY_FILL = 'var(--rv-profile-empty)';

const BAR_H = 18;
const GAP = 2;
// Sex reads better as a part of a whole than as a length.
const PIE_FIELD = 'sex';
// One bucket per ~6px so bars never go sub-pixel.
const PX_PER_BUCKET = 6;

const labelStyle: React.CSSProperties = {
  fontSize: '0.6rem',
  lineHeight: 1.25,
  color: 'var(--rv-muted)',
  fontWeight: 400,
};

const num = (n: number) => n.toLocaleString();
const pct = (n: number) => `${n < 1 && n > 0 ? '<1' : Math.round(n)}%`;

function Label({
  name,
  value,
  strong,
  onHover,
}: {
  name: string;
  value: string;
  strong?: boolean;
  onHover?: (on: boolean) => void;
}) {
  return (
    <div
      onMouseEnter={onHover && (() => onHover(true))}
      onMouseLeave={onHover && (() => onHover(false))}
      style={{
        ...labelStyle,
        display: 'flex',
        gap: 4,
        ...(strong ? { color: 'var(--rv-fg)', fontWeight: 600 } : null),
      }}
    >
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

function Bars({
  buckets,
  max,
  hovered,
  onHover,
}: {
  buckets: number[];
  max: number;
  hovered: number | null;
  onHover: (i: number | null) => void;
}) {
  return (
    <>
      <div
        style={{ display: 'flex', alignItems: 'flex-end', gap: 1, height: BAR_H, width: '100%' }}
      >
        {buckets.map((count, i) => (
          <div
            key={i}
            style={{
              opacity: hovered === null || hovered === i ? 1 : 0.45,
              flex: 1,
              height: `${max === 0 ? 0 : Math.max(count === 0 ? 1 : 8, (count / max) * 100)}%`,
              minHeight: 1,
              background: count === 0 ? EMPTY_FILL : RAMP[0],
              borderRadius: '2px 2px 0 0',
            }}
          />
        ))}
      </div>
      {/* Hit columns cover the whole cell, so the dead space above a short
          bar is a target too. */}
      <div
        style={{ position: 'absolute', inset: 0, display: 'flex', gap: 1 }}
        onMouseLeave={() => onHover(null)}
      >
        {buckets.map((_, i) => (
          <div key={i} style={{ flex: 1 }} onMouseEnter={() => onHover(i)} />
        ))}
      </div>
    </>
  );
}

function StackedBar({
  parts,
  hovered,
  onHover,
}: {
  parts: Array<Segment & { fill: string }>;
  hovered: number | null;
  onHover: (i: number | null) => void;
}) {
  return (
    <div
      style={{ display: 'flex', gap: GAP, height: BAR_H, width: '100%', alignItems: 'stretch' }}
      onMouseLeave={() => onHover(null)}
    >
      {parts.map((p, i) => (
        <div
          key={p.label}
          onMouseEnter={() => onHover(i)}
          style={{
            flex: `${Math.max(p.pct, 1.5)} 0 0`,
            background: p.fill,
            borderRadius: 2,
            // A ring rather than a brighter fill, which would read as a
            // different rank on a single-hue ramp.
            boxShadow: i === hovered ? 'inset 0 0 0 2px var(--rv-fg)' : undefined,
          }}
        />
      ))}
    </div>
  );
}

// Bigger than the bar height: the label lines below usually set the cell's
// real height, so a pie this size fills space that's already there.
const PIE_SIZE = 32;
const GAP_STROKE = 1.5;
// Room for the hover halo to bulge past the true rim.
const HALO_MARGIN = 3;
const HALO_WIDTH = 2;
const SVG_SIZE = PIE_SIZE + HALO_MARGIN * 2;

function wedgePath(cx: number, cy: number, r: number, fromPct: number, toPct: number): string {
  const angle = (pct: number) => (pct / 100) * 2 * Math.PI - Math.PI / 2;
  const point = (a: number): [number, number] => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const [x0, y0] = point(angle(fromPct));
  const [x1, y1] = point(angle(toPct));
  const large = toPct - fromPct > 50 ? 1 : 0;
  return `M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`;
}

// Just the curved boundary, no lines to center, so the halo traces the arc
// without drawing spokes toward the middle.
function arcPath(cx: number, cy: number, r: number, fromPct: number, toPct: number): string {
  const angle = (pct: number) => (pct / 100) * 2 * Math.PI - Math.PI / 2;
  const point = (a: number): [number, number] => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const [x0, y0] = point(angle(fromPct));
  const [x1, y1] = point(angle(toPct));
  const large = toPct - fromPct > 50 ? 1 : 0;
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`;
}

// Slices are not padded up to a minimum the way bar segments are: in a pie the
// area is the encoding, so inflating a sliver would misstate it. A slice too
// small to see still has its label underneath.
//
// Each wedge gets a stroke in the cell background color for spacing between
// slices. Hover draws a separate halo just outside the true rim rather than
// an inset ring, so it never competes with that gap stroke for the same
// pixels.
function Pie({
  parts,
  hovered,
  onHover,
}: {
  parts: Array<Segment & { fill: string }>;
  hovered: number | null;
  onHover: (i: number | null) => void;
}) {
  const c = SVG_SIZE / 2;
  const r = PIE_SIZE / 2 - 1;
  let acc = 0;
  const wedges = parts.map((p, i) => {
    const from = acc;
    // Rounding leaves a hairline of background at the end, so the last slice
    // closes the circle rather than carrying its own percentage.
    acc = i === parts.length - 1 ? 100 : acc + p.pct;
    return { ...p, from, to: acc };
  });
  return (
    <div style={{ display: 'flex', justifyContent: 'center', height: SVG_SIZE }}>
      <svg width={SVG_SIZE} height={SVG_SIZE} onMouseLeave={() => onHover(null)}>
        {wedges.length === 1 ? (
          <circle cx={c} cy={c} r={r} fill={wedges[0].fill} />
        ) : (
          wedges.map((w, i) => (
            <path
              key={w.label}
              d={wedgePath(c, c, r, w.from, w.to)}
              fill={w.fill}
              stroke="var(--rv-surface-2)"
              strokeWidth={GAP_STROKE}
              onMouseEnter={() => onHover(i)}
            />
          ))
        )}
        {hovered !== null && hovered < wedges.length && wedges.length > 1 && (
          <path
            // Inner edge flush with the true rim, extending outward from
            // there, so it touches the disc without overlapping the gap
            // stroke inside it.
            d={arcPath(c, c, r + HALO_WIDTH / 2, wedges[hovered].from, wedges[hovered].to)}
            fill="none"
            stroke="var(--rv-fg)"
            strokeWidth={HALO_WIDTH}
            pointerEvents="none"
          />
        )}
      </svg>
    </div>
  );
}

function Categorical({ parts, pie }: { parts: Array<Segment & { fill: string }>; pie?: boolean }) {
  const [hovered, setHovered] = useState<number | null>(null);
  // parts can shrink out from under a stale hovered index
  const valid = hovered !== null && hovered < parts.length ? hovered : null;
  return (
    <>
      {pie ? (
        <Pie parts={parts} hovered={valid} onHover={setHovered} />
      ) : (
        <StackedBar parts={parts} hovered={valid} onHover={setHovered} />
      )}
      {parts.map((p, i) => (
        <Label
          key={p.label}
          name={p.label}
          value={pct(p.pct)}
          strong={i === valid}
          onHover={(on) => setHovered(on ? i : null)}
        />
      ))}
    </>
  );
}

function Histogram({
  buckets,
  bucketLabels,
  max,
  low,
  high,
}: {
  buckets: number[];
  bucketLabels: string[];
  max: number;
  low: string;
  high: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  // buckets can shrink out from under a stale hovered index
  const valid = hovered !== null && hovered < buckets.length ? hovered : null;
  return (
    <>
      {/* Height reserved whether or not anything is hovered, so the row cannot
          resize as the pointer crosses it. The count anchors by its nearest
          edge rather than centring on the bucket: centring would overflow the
          cell at both ends, and clamping there would leave most buckets sharing
          one position. */}
      <div
        style={{
          ...labelStyle,
          position: 'relative',
          height: '1.25em',
          color: 'var(--rv-fg)',
          fontWeight: 600,
        }}
      >
        {valid !== null && (
          <span
            style={{
              position: 'absolute',
              whiteSpace: 'nowrap',
              ...(valid < buckets.length / 2
                ? { left: `${(valid / buckets.length) * 100}%` }
                : { right: `${((buckets.length - 1 - valid) / buckets.length) * 100}%` }),
            }}
          >
            {num(buckets[valid])}
          </span>
        )}
      </div>
      <Bars buckets={buckets} max={max} hovered={valid} onHover={setHovered} />
      {valid === null ? (
        <RangeLabel low={low} high={high} />
      ) : (
        <CenterLabel text={bucketLabels[valid]} strong />
      )}
    </>
  );
}

function CenterLabel({ text, strong }: { text: string; strong?: boolean }) {
  return (
    <div
      style={{
        ...labelStyle,
        textAlign: 'center',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        ...(strong ? { color: 'var(--rv-fg)', fontWeight: 600 } : null),
      }}
    >
      {text}
    </div>
  );
}

// Pinning bounds to the cell edges says "this spans the whole cell", which is
// true of the idle range and not of one hovered bucket.
function RangeLabel({ low, high }: { low: string; high: string }) {
  if (low === high) return <CenterLabel text={low} />;
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

function ProfileCell({ profile, field }: { profile: Profile; field: string }) {
  if (profile.kind === 'none') return null;

  if (profile.kind === 'identifier') {
    // "unique", not "patients": the four patient identifiers disagree with each
    // other, so "patients" would claim several patient counts for one cohort.
    return (
      <div style={{ textAlign: 'center' }}>
        <Chip text={`${num(profile.distinct)} unique`} />
      </div>
    );
  }

  if (profile.kind === 'categorical') {
    const { segments, other, empty, distinct } = profile;
    const rolledUp = distinct - segments.length;
    const isSex = field === PIE_FIELD;
    // M is always the darkest blue and F the next step down, everywhere sex
    // appears. Anything else just takes what's left; those don't need to be
    // consistent with each other.
    const parts = [
      ...segments.map((s, i) => ({
        ...s,
        fill: isSex
          ? RAMP[s.label === 'M' ? 0 : s.label === 'F' ? 1 : 2]
          : RAMP[Math.min(i, RAMP.length - 1)],
      })),
      ...(other ? [{ ...other, label: `+${num(rolledUp)} more`, fill: OTHER_FILL }] : []),
      ...(empty ? [{ ...empty, fill: EMPTY_FILL }] : []),
    ].filter((p) => p.count > 0);
    return <Categorical parts={parts} pie={isSex} />;
  }

  if (profile.kind === 'numeric') {
    const { buckets, bucketLabels, max, min, hi } = profile;
    return (
      <Histogram
        buckets={buckets}
        bucketLabels={bucketLabels}
        max={max}
        low={num(min)}
        high={num(hi)}
      />
    );
  }

  const { buckets, bucketLabels, max, first, last } = profile;
  return (
    <Histogram buckets={buckets} bucketLabels={bucketLabels} max={max} low={first} high={last} />
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
              <ProfileCell profile={profile} field={col.id} />
            </div>
          </td>
        );
      })}
    </tr>
  );
}
