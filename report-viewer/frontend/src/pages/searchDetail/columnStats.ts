import { columnKind, type ColumnKind } from './columnKind';

type Row = Record<string, unknown>;

export type Segment = { label: string; count: number; pct: number };

export type Profile =
  | {
      kind: 'numeric';
      buckets: number[];
      bucketBounds: Array<[string, string]>;
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
      bucketBounds: Array<[string, string]>;
      max: number;
      first: string;
      last: string;
    }
  | { kind: 'identifier'; distinct: number }
  | { kind: 'none' };

function bucketize(values: number[], min: number, max: number, count: number): number[] {
  const buckets = new Array<number>(count).fill(0);
  const span = max - min;
  for (const v of values) {
    const idx = span === 0 ? 0 : Math.min(count - 1, Math.floor(((v - min) / span) * count));
    buckets[idx] += 1;
  }
  return buckets;
}

function numericProfile(values: unknown[], widthAllows: number): Profile {
  const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (nums.length === 0) return { kind: 'none' };
  const sorted = [...nums].sort((a, b) => a - b);
  const min = sorted[0];
  const hi = sorted[sorted.length - 1];
  const count = bucketsFor(nums.length, hi > min, widthAllows);
  const step = (hi - min) / count;
  const fmt = (n: number) => (step >= 1 ? String(Math.round(n)) : n.toFixed(1));
  const buckets = bucketize(nums, min, hi, count);
  return {
    kind: 'numeric',
    buckets,
    bucketBounds: bucketBounds(min, hi, count, fmt),
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

// Time buckets are calendar intervals, not equal slices of the span. An equal
// slice starting mid-March cannot be named, so it needs both bounds spelled out
// and they do not fit the column; a whole month is named completely by
// "2021-03". Same ladder-and-grain approach d3 and Vega-Lite take.
type Grain = { slice: [number, number]; coarser: Grain | null };

const G_YEAR: Grain = { slice: [0, 4], coarser: null };
const G_MONTH: Grain = { slice: [0, 7], coarser: G_YEAR };
const G_DATE: Grain = { slice: [0, 10], coarser: G_MONTH };
const G_TIME: Grain = { slice: [11, 16], coarser: G_DATE };

const iso = (t: number, [from, to]: [number, number]) => new Date(t).toISOString().slice(from, to);

type Interval = {
  width: number;
  floor: (t: number) => number;
  next: (t: number) => number;
  grain: Grain;
};

// Every fixed step divides a day evenly and the epoch is a UTC midnight, so
// flooring against it lands on a real clock boundary.
function stepInterval(ms: number, grain: Grain): Interval {
  return { width: ms, floor: (t) => Math.floor(t / ms) * ms, next: (t) => t + ms, grain };
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
  };
}

function yearInterval(years: number): Interval {
  return {
    width: years * 365 * DAY,
    floor: (t) => Date.UTC(Math.floor(new Date(t).getUTCFullYear() / years) * years, 0, 1),
    next: (t) => Date.UTC(new Date(t).getUTCFullYear() + years, 0, 1),
    grain: G_YEAR,
  };
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

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

// A clock-time label only says which day it belongs to via the idle line, so
// sub-day rungs are limited to cohorts that sit inside one day.
//
// Widths above a day are approximate, so the chosen rung can overshoot the
// target by a bucket or two. Close enough for a bar count, and never unbounded.
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

  const edges = [interval.floor(first)];
  while (edges[edges.length - 1] <= last) edges.push(interval.next(edges[edges.length - 1]));

  const buckets = new Array<number>(edges.length - 1).fill(0);
  let i = 0;
  for (const t of times) {
    while (i + 1 < buckets.length && t >= edges[i + 1]) i += 1;
    buckets[i] += 1;
  }

  // Bucket labels carry the fine detail, so the idle line carries one step of
  // coarser context instead of repeating it in every bucket. On a cohort inside
  // a single interval both ends match and it collapses to one label.
  const context = interval.grain.coarser ?? interval.grain;
  return {
    kind: 'temporal',
    buckets,
    bucketBounds: edges
      .slice(0, -1)
      .map((e): [string, string] => [iso(e, interval.grain.slice), iso(e, interval.grain.slice)]),
    max: Math.max(...buckets),
    first: iso(first, context.slice),
    last: iso(last, context.slice),
  };
}

function identifierProfile(values: unknown[]): Profile {
  const distinct = new Set(values.map((v) => String(v)));
  return { kind: 'identifier', distinct: distinct.size };
}

// Bounds as a pair, so the hover readout can pin them to the cell edges the way
// the idle min and max line does.
function bucketBounds(
  lo: number,
  hi: number,
  count: number,
  fmt: (n: number) => string,
): Array<[string, string]> {
  const step = (hi - lo) / count;
  return Array.from({ length: count }, (_, i) => [
    fmt(lo + step * i),
    fmt(i === count - 1 ? hi : lo + step * (i + 1)),
  ]);
}

// Square-root binning capped by what the column width can show. No floor: one
// value gets one bucket, so the bar fills the cell instead of sitting at the
// left as if it were a low reading. No spread means one bucket for the same
// reason.
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
