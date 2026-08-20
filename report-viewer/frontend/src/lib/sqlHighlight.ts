// Highlight what the SQL actually matched, rather than the literal `match_terms`.
//
// `match_terms` are plain strings the model derives from its own regex, matched with
// word boundaries. The SQL matches synonyms, morphological variants and either word
// order across a gap, so the two disagree constantly: a pattern that selected
// "nodules in the left lung" yields the term "pulmonary nodule", which appears
// nowhere in that sentence. Reading the positive patterns back out of the SQL keeps
// the highlight and the row selection in agreement.

/** A Trino string literal, with '' as the escaped single quote. */
function readLiteral(sql: string, from: number): { value: string; end: number } | null {
  if (sql[from] !== "'") return null;
  let out = '';
  for (let i = from + 1; i < sql.length; i++) {
    if (sql[i] !== "'") {
      out += sql[i];
      continue;
    }
    if (sql[i + 1] === "'") {
      out += "'";
      i++;
      continue;
    }
    return { value: out, end: i };
  }
  return null;
}

/** True when this REGEXP_LIKE is the argument of a NOT — i.e. a negation veto. */
function isNegated(sql: string, at: number): boolean {
  const before = sql.slice(Math.max(0, at - 200), at);
  return /\bNOT\s*$/i.test(before);
}

/** Turn capturing groups into non-capturing ones. `split()` keys off capture-group
 *  position, so a stray group inside a pattern would corrupt the highlight offsets. */
function decapture(pattern: string): string {
  let out = '';
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === '\\') {
      out += c + (pattern[i + 1] ?? '');
      i++;
      continue;
    }
    if (c === '[') {
      // Character class: copy verbatim, ']' first position is a literal.
      let j = i + 1;
      if (pattern[j] === '^') j++;
      if (pattern[j] === ']') j++;
      while (j < pattern.length && pattern[j] !== ']') {
        if (pattern[j] === '\\') j++;
        j++;
      }
      out += pattern.slice(i, j + 1);
      i = j;
      continue;
    }
    out += c === '(' && pattern[i + 1] !== '?' ? '(?:' : c;
  }
  return out;
}

/** Strip leading inline flags — JS has no `(?is)` — and report them separately. */
function splitInlineFlags(pattern: string): { body: string; flags: string } {
  const m = /^\(\?([a-zA-Z]+)\)/.exec(pattern);
  if (!m) return { body: pattern, flags: '' };
  return { body: pattern.slice(m[0].length), flags: m[1].replace(/[^ism]/g, '') };
}

/** Columns whose contents the row-detail view actually renders. A REGEXP_LIKE on
 *  anything else -- `service_name`, say -- filters the cohort but says nothing about
 *  the report body, and highlighting its pattern marks stray words like "lung". */
const TEXT_COLUMN = /report_text|report_section_/i;

/** Every non-negated REGEXP_LIKE pattern applied to a rendered text column. */
export function positivePatterns(sql: string): string[] {
  const out: string[] = [];
  const re = /REGEXP_LIKE\s*\(/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(sql))) {
    if (isNegated(sql, m.index)) continue;
    // The pattern is the call's last string literal, so keep the last one seen
    // before the parens balance again.
    let depth = 1;
    let last: string | null = null;
    let lastAt = -1;
    const start = m.index + m[0].length;
    let i = start;
    for (; i < sql.length && depth > 0; i++) {
      const c = sql[i];
      if (c === "'") {
        const lit = readLiteral(sql, i);
        if (!lit) break;
        last = lit.value;
        lastAt = i;
        i = lit.end;
      } else if (c === '(') depth++;
      else if (c === ')') depth--;
    }
    // Everything before the pattern literal is the column expression being tested.
    if (last && lastAt > start && TEXT_COLUMN.test(sql.slice(start, lastAt))) out.push(last);
  }
  return out;
}

/** One global regex with exactly one capture group, or null if nothing usable. */
export function highlightRegexFromSql(sql: string | undefined | null): RegExp | null {
  if (!sql) return null;
  const bodies: string[] = [];
  let flags = '';
  for (const p of positivePatterns(sql)) {
    if (p.length > 2000) continue; // runaway pattern; not worth compiling
    const { body, flags: f } = splitInlineFlags(p);
    if (!body) continue;
    bodies.push(decapture(body));
    for (const c of f) if (!flags.includes(c)) flags += c;
  }
  if (!bodies.length) return null;
  const unique = Array.from(new Set(bodies));
  try {
    const re = new RegExp(`(${unique.join('|')})`, flags.replace('m', '') + 'g');
    // A pattern matching the empty string would split every character.
    if (re.test('')) return null;
    re.lastIndex = 0;
    return re;
  } catch {
    return null; // dialect gap (Joni vs JS) — caller falls back to match_terms
  }
}
