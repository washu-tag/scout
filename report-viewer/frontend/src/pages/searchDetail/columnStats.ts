import { columnKind, type ColumnKind } from './columnKind';

type Row = Record<string, unknown>;

export type Segment = { label: string; count: number; pct: number };

export type Profile =
  | {
      kind: 'numeric';
      buckets: number[];
      bucketLabels: string[];
      max: number;
      min: number;
      hi: number;
      median: number;
      nonNull: number;
      total: number;
    }
  | {
      kind: 'categorical';
      segments: Segment[];
      other: Segment | null;
      empty: Segment | null;
      distinct: number;
      total: number;
    }
  | {
      kind: 'temporal';
      buckets: number[];
      bucketLabels: string[];
      max: number;
      first: string;
      last: string;
      nonNull: number;
      total: number;
    }
  | { kind: 'identifier'; distinct: number; nonNull: number; total: number }
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

function median(sorted: number[]): number {
  if (sorted.length === 0) return 0;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function bucketEdgeLabels(
  lo: number,
  hi: number,
  count: number,
  fmt: (n: number) => string,
): string[] {
  const step = (hi - lo) / count;
  return Array.from({ length: count }, (_, i) => {
    if (step === 0) return fmt(lo);
    const from = fmt(lo + step * i);
    const to = fmt(i === count - 1 ? hi : lo + step * (i + 1));
    return from === to ? from : `${from} to ${to}`;
  });
}

function numericProfile(values: unknown[], total: number, bucketCount: number): Profile {
  const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (nums.length === 0) return { kind: 'none' };
  const sorted = [...nums].sort((a, b) => a - b);
  const min = sorted[0];
  const hi = sorted[sorted.length - 1];
  const buckets = bucketize(nums, min, hi, bucketCount);
  const step = (hi - min) / bucketCount;
  const fmt = (n: number) => (step >= 1 ? String(Math.round(n)) : n.toFixed(1));
  return {
    kind: 'numeric',
    buckets,
    bucketLabels: bucketEdgeLabels(min, hi, bucketCount, fmt),
    max: Math.max(...buckets),
    min,
    hi,
    median: median(sorted),
    nonNull: nums.length,
    total,
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
    total,
  };
}

function temporalProfile(values: unknown[], total: number, bucketCount: number): Profile {
  const times: number[] = [];
  for (const v of values) {
    const t = typeof v === 'string' || typeof v === 'number' ? new Date(v).getTime() : NaN;
    if (Number.isFinite(t)) times.push(t);
  }
  if (times.length === 0) return { kind: 'none' };
  times.sort((a, b) => a - b);
  const first = times[0];
  const last = times[times.length - 1];
  const buckets = bucketize(times, first, last, bucketCount);
  const asDate = (n: number) => new Date(n).toISOString().slice(0, 10);
  return {
    kind: 'temporal',
    buckets,
    bucketLabels: bucketEdgeLabels(first, last, bucketCount, asDate),
    max: Math.max(...buckets),
    first: asDate(first),
    last: asDate(last),
    nonNull: times.length,
    total,
  };
}

function identifierProfile(values: unknown[], total: number): Profile {
  const distinct = new Set(values.map((v) => String(v)));
  return { kind: 'identifier', distinct: distinct.size, nonNull: values.length, total };
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
  if (kind === 'numeric') return numericProfile(present, rows.length, bucketCount);
  if (kind === 'temporal') return temporalProfile(present, rows.length, bucketCount);
  return identifierProfile(present, rows.length);
}
