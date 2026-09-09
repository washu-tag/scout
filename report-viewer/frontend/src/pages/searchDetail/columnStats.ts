import { columnKind, type ColumnKind } from './columnKind';

type Row = Record<string, unknown>;

export type Segment = { label: string; count: number; pct: number };

// Range separator
const RANGE = ' – ';

export type Profile =
  | {
      kind: 'numeric';
      buckets: number[];
      bucketLabels: string[];
      max: number;
      min: number;
      hi: number;
    }
  | {
      kind: 'categorical';
      segments: Segment[];
      other: Segment | null;
      empty: Segment | null;
      distinct: number;
    }
  | {
      kind: 'temporal';
      buckets: number[];
      bucketLabels: string[];
      max: number;
      first: string;
      last: string;
    }
  | { kind: 'identifier'; distinct: number }
  | { kind: 'none' };

// Edges tile the axis, so the last one sits past the final value. Both profiles
// snap their first edge to a round boundary, which means the edges can reach
// outside the data; the idle line carries the true min and max instead.
function buildEdges(first: number, last: number, next: (t: number) => number): number[] {
  const edges = [first];
  while (edges[edges.length - 1] <= last) edges.push(next(edges[edges.length - 1]));
  return edges;
}

// Values must be sorted, so the edge index only ever moves forward.
function countByEdges(values: number[], edges: number[]): number[] {
  const buckets = new Array<number>(edges.length - 1).fill(0);
  let i = 0;
  for (const v of values) {
    while (i + 1 < buckets.length && v >= edges[i + 1]) i += 1;
    buckets[i] += 1;
  }
  return buckets;
}

// 1, 2, 5, or 10 times a power of ten: the smallest step that keeps the
// bucket count within target, so ages land on round bands like 5s and 10s.
function niceStep(span: number, target: number): number {
  const rough = span / target;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  return ([1, 2, 5].find((m) => rough <= m * magnitude) ?? 10) * magnitude;
}

function numericProfile(values: unknown[], widthAllows: number): Profile {
  const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (nums.length === 0) return { kind: 'none' };
  const sorted = [...nums].sort((a, b) => a - b);
  const min = sorted[0];
  const hi = sorted[sorted.length - 1];

  const target = bucketsFor(nums.length, hi > min, widthAllows);
  // An integer column gets an integer step, so a narrow age range bins in years
  // rather than in halves of one.
  const rough = hi > min ? niceStep(hi - min, target) : 1;
  const step = sorted.every(Number.isInteger) ? Math.max(1, rough) : rough;
  const edges = buildEdges(Math.floor(min / step) * step, hi, (v) => v + step);

  // Round to the step, which both suits the granularity and clears the float
  // noise that dividing by a fractional step leaves on the edges.
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  const fmt = (n: number) => n.toFixed(decimals);
  const buckets = countByEdges(sorted, edges);
  return {
    kind: 'numeric',
    buckets,
    // One value is not a range.
    bucketLabels:
      hi > min
        ? edges.slice(0, -1).map((e, i) => `${fmt(e)}${RANGE}${fmt(edges[i + 1])}`)
        : [fmt(min)],
    max: Math.max(...buckets),
    min,
    hi,
  };
}

// Row height is fixed, so labels are capped. `empty` and the rolled-up tail
// each claim a line when present, named values fill the rest.
const MAX_LABEL_LINES = 3;

function categoricalProfile(rows: Row[], field: string): Profile {
  const counts = new Map<string, number>();
  let empty = 0;
  for (const row of rows) {
    const v = row[field];
    if (v == null || v === '') {
      empty += 1;
      continue;
    }
    const key = String(v);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  if (counts.size === 0) return { kind: 'none' };
  const total = rows.length;
  const pct = (n: number) => (total === 0 ? 0 : (n / total) * 100);
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

  let named = MAX_LABEL_LINES - (empty > 0 ? 1 : 0);
  if (counts.size > named) named -= 1;
  named = Math.max(1, named);

  const segments: Segment[] = ranked
    .slice(0, named)
    .map(([label, count]) => ({ label, count, pct: pct(count) }));
  const otherCount = ranked.slice(named).reduce((sum, [, count]) => sum + count, 0);
  return {
    kind: 'categorical',
    segments,
    other: otherCount > 0 ? { label: 'other', count: otherCount, pct: pct(otherCount) } : null,
    empty: empty > 0 ? { label: 'empty', count: empty, pct: pct(empty) } : null,
    distinct: counts.size,
  };
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

// Time buckets are calendar intervals, not equal slices of the span. An equal
// slice starting mid-March has no name, while a whole month is named
// completely by "2021-03".
//
// `unit` is how much time one label at this grain covers, which decides
// whether a bucket needs its end spelled out.
type Grain = { slice: [number, number]; unit: number; coarser: Grain | null };

const G_YEAR: Grain = { slice: [0, 4], unit: 365 * DAY, coarser: null };
const G_MONTH: Grain = { slice: [0, 7], unit: 30 * DAY, coarser: G_YEAR };
const G_DATE: Grain = { slice: [0, 10], unit: DAY, coarser: G_MONTH };
const G_TIME: Grain = { slice: [11, 16], unit: MINUTE, coarser: G_DATE };

const iso = (t: number, [from, to]: [number, number]) => new Date(t).toISOString().slice(from, to);

type Interval = {
  width: number;
  floor: (t: number) => number;
  next: (t: number) => number;
  grain: Grain;
  // A label names the whole bucket only when it spans one unit of its grain.
  // Wider buckets get a range instead: only one label is ever visible, so
  // "2025-09-25" on a week-wide bucket would read as a single day.
  oneUnit: boolean;
};

// Every fixed step divides a day evenly and the epoch is a UTC midnight, so
// flooring against it lands on a real clock boundary.
function stepInterval(ms: number, grain: Grain): Interval {
  return {
    width: ms,
    floor: (t) => Math.floor(t / ms) * ms,
    next: (t) => t + ms,
    grain,
    oneUnit: ms === grain.unit,
  };
}

function monthInterval(months: number): Interval {
  return {
    width: months * 30 * DAY,
    floor: (t) => {
      const d = new Date(t);
      return Date.UTC(d.getUTCFullYear(), Math.floor(d.getUTCMonth() / months) * months, 1);
    },
    next: (t) => {
      const d = new Date(t);
      return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, 1);
    },
    grain: G_MONTH,
    oneUnit: months === 1,
  };
}

function yearInterval(years: number): Interval {
  return {
    width: years * 365 * DAY,
    floor: (t) => Date.UTC(Math.floor(new Date(t).getUTCFullYear() / years) * years, 0, 1),
    next: (t) => Date.UTC(new Date(t).getUTCFullYear() + years, 0, 1),
    grain: G_YEAR,
    oneUnit: years === 1,
  };
}

const LADDER: Interval[] = [
  stepInterval(MINUTE, G_TIME),
  stepInterval(5 * MINUTE, G_TIME),
  stepInterval(15 * MINUTE, G_TIME),
  stepInterval(30 * MINUTE, G_TIME),
  stepInterval(HOUR, G_TIME),
  stepInterval(3 * HOUR, G_TIME),
  stepInterval(6 * HOUR, G_TIME),
  stepInterval(12 * HOUR, G_TIME),
  stepInterval(DAY, G_DATE),
  stepInterval(2 * DAY, G_DATE),
  stepInterval(7 * DAY, G_DATE),
  monthInterval(1),
  monthInterval(3),
  yearInterval(1),
  yearInterval(5),
  yearInterval(10),
];

// Sub-day rungs are limited to cohorts inside a single day, since a clock-time
// label only says which day it belongs to via the idle line.
//
// Widths above a day are approximate, so the chosen rung can overshoot the
// target by a bucket or two, which is close enough for a bar count.
function pickInterval(first: number, last: number, target: number): Interval {
  const withinOneDay = iso(first, G_DATE.slice) === iso(last, G_DATE.slice);
  const usable = LADDER.filter((iv) => withinOneDay || iv.grain !== G_TIME);
  return usable.find((iv) => (last - first) / iv.width <= target) ?? usable[usable.length - 1];
}

function temporalProfile(values: unknown[], widthAllows: number): Profile {
  const times: number[] = [];
  for (const v of values) {
    const t = typeof v === 'string' || typeof v === 'number' ? new Date(v).getTime() : NaN;
    if (Number.isFinite(t)) times.push(t);
  }
  if (times.length === 0) return { kind: 'none' };
  times.sort((a, b) => a - b);
  const first = times[0];
  const last = times[times.length - 1];
  const interval = pickInterval(first, last, bucketsFor(times.length, last > first, widthAllows));
  const edges = buildEdges(interval.floor(first), last, interval.next);
  const buckets = countByEdges(times, edges);

  // The idle line carries one step of coarser context instead of repeating
  // what the bucket labels already show. Inside a single interval, both ends
  // match and it collapses to one label.
  const context = interval.grain.coarser ?? interval.grain;
  const label = (t: number) => iso(t, interval.grain.slice);
  return {
    kind: 'temporal',
    buckets,
    // The end is the last instant inside the bucket, not the next bucket's
    // start, so consecutive labels do not appear to share a day.
    bucketLabels: edges
      .slice(0, -1)
      .map((e, i) =>
        interval.oneUnit ? label(e) : `${label(e)}${RANGE}${label(edges[i + 1] - 1)}`,
      ),
    max: Math.max(...buckets),
    first: iso(first, context.slice),
    last: iso(last, context.slice),
  };
}

function identifierProfile(values: unknown[]): Profile {
  const distinct = new Set(values.map((v) => String(v)));
  return { kind: 'identifier', distinct: distinct.size };
}

// Square-root binning capped by what the column width can show. No floor: one
// value gets one bucket so the bar fills the cell instead of reading as a low
// value. No spread also collapses to one bucket.
function bucketsFor(n: number, spread: boolean, widthAllows: number): number {
  if (!spread) return 1;
  return Math.max(1, Math.min(widthAllows, Math.ceil(Math.sqrt(n))));
}

export function profileColumn(
  field: string,
  rows: Row[],
  isDate: boolean,
  bucketCount: number,
): Profile {
  const kind: ColumnKind = columnKind(field, rows, isDate);
  if (kind === 'none') return { kind: 'none' };
  if (kind === 'categorical') return categoricalProfile(rows, field);

  const present = rows.map((r) => r[field]).filter((v) => v != null && v !== '');
  if (present.length === 0) return { kind: 'none' };
  if (kind === 'numeric') return numericProfile(present, bucketCount);
  if (kind === 'temporal') return temporalProfile(present, bucketCount);
  return identifierProfile(present);
}
