// Reading the cell values a search's SQL returns: numbers, and dates either as
// Trino returns them or as the SQL formatted them.

export const ISO_DATEISH = /^\d{4}-\d{2}-\d{2}([T ]|$)/;
// Trino DECIMALs arrive as Python Decimal strings ("10.50", "1E-7"); requiring a
// decimal point or a signed exponent keeps a numeric-looking identifier categorical.
const DECIMAL_STRING = /^-?\d+(?:\.\d+(?:E[+-]\d+)?|E[+-]\d+)$/;

/** A JSON number or a Trino DECIMAL string as a number; undefined otherwise. */
export function toNumber(v: unknown): number | undefined {
  if (typeof v === 'number') return Number.isFinite(v) ? v : undefined;
  if (typeof v === 'string' && DECIMAL_STRING.test(v)) return Number(v);
  return undefined;
}

// The lake is UTC, so an ISO timestamp without an offset is read as UTC rather
// than the browser's local time.
const ISO_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/** Epoch ms for a value matching ISO_DATEISH; undefined if it doesn't parse. */
export function isoTimestamp(v: string): number | undefined {
  let s = v.replace(' ', 'T').replace(/\s*UTC$/i, 'Z');
  if (s.length > 10 && !ISO_ZONE.test(s)) s += 'Z';
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : undefined;
}

// Dates the SQL formatted itself, read month first (when numeric) and as UTC.
const TIME = String.raw`(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AP]M)?)?`;
const MON = String.raw`([A-Za-z]{3})[A-Za-z]*\.?`;
const WEEKDAY = /^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+/i;
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

// Each picks out [year, month, day]; a trailing TIME is always groups 4-7.
const FORMATS: Array<[RegExp, (m: RegExpExecArray) => [string, string, string]]> = [
  // 11/06/2026, 11-06-2026 02:30:00 PM
  [
    new RegExp(String.raw`^(\d{1,2})[/-](\d{1,2})[/-](\d{4})${TIME}$`, 'i'),
    (m) => [m[3], m[1], m[2]],
  ],
  // 2026/11/06
  [new RegExp(String.raw`^(\d{4})/(\d{1,2})/(\d{1,2})${TIME}$`, 'i'), (m) => [m[1], m[2], m[3]]],
  // Nov 6, 2026
  [new RegExp(String.raw`^${MON} (\d{1,2}),? (\d{4})${TIME}$`, 'i'), (m) => [m[3], m[1], m[2]]],
  // 6 Nov 2026, 06-NOV-2026 14:30
  [new RegExp(String.raw`^(\d{1,2})[ -]${MON}[ -](\d{4})${TIME}$`, 'i'), (m) => [m[3], m[2], m[1]]],
  // Nov 2026, November, 2026, Nov-2026, 11/2026, 2026-11
  [new RegExp(String.raw`^${MON},?[ -](\d{4})$`, 'i'), (m) => [m[2], m[1], '1']],
  [/^(\d{1,2})[/-](\d{4})$/i, (m) => [m[2], m[1], '1']],
  [/^(\d{4})-(\d{2})$/i, (m) => [m[1], m[2], '1']],
];

function utc(y: number, mo: number, d: number, h = 0, mi = 0, s = 0): number | undefined {
  if (h > 23 || mi > 59 || s > 59) return undefined;
  const t = Date.UTC(y, mo - 1, d, h, mi, s);
  const back = new Date(t);
  return back.getUTCMonth() === mo - 1 && back.getUTCDate() === d ? t : undefined;
}

function fromParts([y, mo, d]: [string, string, string], m: RegExpExecArray): number | undefined {
  const month = /^\d+$/.test(mo) ? Number(mo) : MONTHS.indexOf(mo.toLowerCase()) + 1;
  if (month < 1 || month > 12) return undefined;
  let h = Number(m[4] ?? 0);
  if (m[7]) {
    if (h < 1 || h > 12) return undefined;
    h = (h % 12) + (m[7].toUpperCase() === 'PM' ? 12 : 0);
  }
  return utc(Number(y), month, Number(d), h, Number(m[5] ?? 0), Number(m[6] ?? 0));
}

/** Epoch ms for an ISO date or a common formatted date; undefined otherwise. */
export function toTimestamp(v: unknown): number | undefined {
  if (typeof v !== 'string') return undefined;
  if (ISO_DATEISH.test(v)) return isoTimestamp(v);
  const s = v.trim().replace(WEEKDAY, '');
  for (const [re, ymd] of FORMATS) {
    const m = re.exec(s);
    if (m) return fromParts(ymd(m), m);
  }
  return undefined;
}
