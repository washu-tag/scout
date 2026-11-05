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

// Trino returns `timestamp with time zone` as `YYYY-MM-DDTHH:MM:SS+00:00`
// (everything in the lake is UTC). Trim seconds and TZ offset for the UI.
export function fmtDate(v: unknown): string {
  if (!v) return '-';
  const d = new Date(String(v));
  if (isNaN(d.getTime())) return String(v);
  const iso = d.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}
