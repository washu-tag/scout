// Match stats over the loaded cohort. The SPA already holds every row, so this
// is a reduce rather than a second query, and the counts are exact rather than
// a truncated top-N.

type Row = Record<string, unknown>;

export type Tally = { label: string; count: number };

export type EvidenceStats = {
  total: number;
  sources: Tally[];
  excluded: number;
  /** text|code x clean|negative. The code+negative cell is the one that matters:
   *  a diagnosis code admitted the row and its own report text disagrees. */
  crosstab: { source: 'text' | 'diagnosis_code'; clean: number; negative: number }[];
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
  const sources = new Map<string, number>();
  const include = new Map<string, number>();
  const exclude = new Map<string, number>();
  let excluded = 0;
  const cross = {
    text: { clean: 0, negative: 0 },
    diagnosis_code: { clean: 0, negative: 0 },
  };

  for (const row of rows) {
    const source = row['ev_source'] == null ? 'unknown' : String(row['ev_source']);
    sources.set(source, (sources.get(source) ?? 0) + 1);

    // Collapse whitespace so the same phrase wrapped across lines tallies once.
    const inc = String(row['ev_positive_span'] ?? '')
      .replace(/\s+/g, ' ')
      .trim();
    if (inc) include.set(inc, (include.get(inc) ?? 0) + 1);
    const neg = String(row['ev_negative_span'] ?? '')
      .replace(/\s+/g, ' ')
      .trim();
    if (neg) {
      exclude.set(neg, (exclude.get(neg) ?? 0) + 1);
      excluded += 1;
    }
    if (source === 'text' || source === 'diagnosis_code') {
      cross[source][neg ? 'negative' : 'clean'] += 1;
    }
  }

  const rankedInclude = rank(include);
  const rankedExclude = rank(exclude);
  return {
    total: rows.length,
    sources: rank(sources),
    excluded,
    crosstab: [
      { source: 'text' as const, ...cross.text },
      { source: 'diagnosis_code' as const, ...cross.diagnosis_code },
    ].filter((r) => r.clean + r.negative > 0),
    positiveSpans: rankedInclude,
    distinctPositive: rankedInclude.length,
    negativeSpans: rankedExclude,
    distinctNegative: rankedExclude.length,
  };
}
