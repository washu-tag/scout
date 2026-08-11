// The search SQL projects whatever it likes, so columns are typed by sniffing
// values rather than from a static list.

export type ColumnKind = 'numeric' | 'categorical' | 'temporal' | 'identifier' | 'none';

// Identifiers: never summarized as a distribution, never a measure.
export const IDENTIFIER_FIELDS = new Set([
  'primary_report_identifier',
  'accession_number',
  'epic_mrn',
  'resolved_epic_mrn',
  'patient_mpi',
  'resolved_mpi',
  'study_instance_uid',
]);

const SAMPLE_SIZE = 200;
const ISO_DATEISH = /^\d{4}-\d{2}-\d{2}([T ]|$)/;

type Row = Record<string, unknown>;

function sampleValues(field: string, rows: Row[]): unknown[] {
  const out: unknown[] = [];
  for (const row of rows) {
    const v = row[field];
    if (v == null || v === '') continue;
    out.push(v);
    if (out.length >= SAMPLE_SIZE) break;
  }
  return out;
}

/** `isDate` comes from the table's column config; everything else is inferred. */
export function columnKind(field: string, rows: Row[], isDate = false): ColumnKind {
  const sample = sampleValues(field, rows);

  // Arrays and structs have no scalar axis. Nothing projects one today, but a
  // future projection would otherwise render as stringified objects.
  if (sample.some((v) => typeof v === 'object')) return 'none';

  if (IDENTIFIER_FIELDS.has(field)) return 'identifier';
  if (isDate) return 'temporal';
  if (sample.length === 0) return 'none';

  // Trino numerics arrive as JSON numbers, so a numeric-looking identifier
  // stays a string and lands as categorical.
  if (sample.every((v) => typeof v === 'number')) return 'numeric';
  if (sample.every((v) => typeof v === 'string' && ISO_DATEISH.test(v))) return 'temporal';
  return 'categorical';
}
