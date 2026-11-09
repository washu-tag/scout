import { ISO_DATEISH, isoTimestamp } from '../../values';

/** `dx_count` → `Dx count`; a quoted alias like `Patient Count` is left as written. */
export function fmtHeader(field: string): string {
  const words = field.replace(/_+/g, ' ').trim();
  return words ? words[0].toUpperCase() + words.slice(1) : field;
}

export function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

// An ISO timestamp, with an offset or read as UTC without one, shows as
// `YYYY-MM-DD HH:MM` UTC. Anything else, such as a bare date or one the SQL
// formatted itself, is shown as written.
export function fmtDate(v: unknown): string {
  if (!v) return '-';
  if (typeof v !== 'string' || !ISO_DATEISH.test(v) || v.length === 10) return String(v);
  const t = isoTimestamp(v);
  if (t === undefined) return String(v);
  const iso = new Date(t).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}
