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

// Case-insensitive patterns mean "Stroke" and "stroke" arrive as separate
// spans. One bucket per phrase, first spelling labels it, so CVA stays CVA.
function rank(values: string[]): Tally[] {
  const counts = new Map<string, number>();
  const labels = new Map<string, string>();
  for (const value of values) {
    const key = value.toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
    if (!labels.has(key)) labels.set(key, value);
  }
  return [...counts.entries()]
    .map(([key, count]) => ({ label: labels.get(key) ?? key, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function hasEvidence(rows: Row[]): boolean {
  return rows.length > 0 && rows[0]['ev_source'] !== undefined;
}

export function evidenceStats(rows: Row[]): EvidenceStats {
  const include: string[] = [];
  const exclude: string[] = [];
  const tally: Record<Category, { rows: number; negative: number }> = {
    text_and_code: { rows: 0, negative: 0 },
    text: { rows: 0, negative: 0 },
    diagnosis_code: { rows: 0, negative: 0 },
    unknown: { rows: 0, negative: 0 },
  };

  for (const row of rows) {
    const category = evidenceCategory(row);
    const inc = collapse(row['ev_positive_span']);
    if (inc) include.push(inc);
    const neg = collapse(row['ev_negative_span']);
    if (neg) {
      exclude.push(neg);
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
