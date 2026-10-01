// Match stats over the loaded cohort. The SPA already holds every row, so this
// is a reduce rather than a second query, and the counts are exact.

import { EV_CATEGORIES, collapse, evidenceCategory, type EvCategory } from '../../api/client';

type Row = Record<string, unknown>;

export type Tally = { label: string; count: number };

export type Category = EvCategory;

export type EvidenceStats = {
  total: number;
  breakdown: { category: Category; rows: number; negative: number }[];
  positiveSpans: Tally[];
  distinctPositive: number;
  negativeSpans: Tally[];
  distinctNegative: number;
};

function rank(counts: Map<string, number>): Tally[] {
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function hasEvidence(rows: Row[]): boolean {
  return rows.length > 0 && rows[0]['ev_source'] !== undefined;
}

export function evidenceStats(rows: Row[]): EvidenceStats {
  const include = new Map<string, number>();
  const exclude = new Map<string, number>();
  const tally: Record<Category, { rows: number; negative: number }> = {
    text_and_code: { rows: 0, negative: 0 },
    text: { rows: 0, negative: 0 },
    diagnosis_code: { rows: 0, negative: 0 },
    unknown: { rows: 0, negative: 0 },
  };

  for (const row of rows) {
    const category = evidenceCategory(row);
    const inc = collapse(row['ev_positive_span']);
    if (inc) include.set(inc, (include.get(inc) ?? 0) + 1);
    const neg = collapse(row['ev_negative_span']);
    if (neg) {
      exclude.set(neg, (exclude.get(neg) ?? 0) + 1);
      tally[category].negative += 1;
    }
    tally[category].rows += 1;
  }

  const rankedInclude = rank(include);
  const rankedExclude = rank(exclude);
  return {
    total: rows.length,
    breakdown: EV_CATEGORIES.map((category) => ({ category, ...tally[category] })).filter(
      (r) => r.rows > 0,
    ),
    positiveSpans: rankedInclude,
    distinctPositive: rankedInclude.length,
    negativeSpans: rankedExclude,
    distinctNegative: rankedExclude.length,
  };
}
