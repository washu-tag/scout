import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getMatchedSpans } from '../../api/client';
import { Modal } from '../../Modal';

export function ExplainSqlModal(props: {
  explanation: string;
  sql: string;
  highlightTerms: string[];
  highlightDiagnosis: string[];
  onClose: () => void;
  /** EXPERIMENTAL: enables the matched-text breakdown. */
  searchId?: string;
}) {
  const terms = props.highlightTerms.filter((t) => t.trim().length > 0);
  // Gate the accuracy caveat on the SQL, not on match_terms: those are a model-supplied
  // hint it can omit. A cohort built only from diagnosis codes reads a structured field
  // and is exact, so the caveat would be untrue there.
  const matchesText = /REGEXP_LIKE/i.test(props.sql);
  const codes = props.highlightDiagnosis.filter((d) => d.trim().length > 0);
  const [copied, setCopied] = useState(false);
  const onCopySql = () => {
    if (!props.sql) return;
    // execCommand is deprecated but unavoidable: navigator.clipboard is
    // blocked by OWUI's artifact-iframe Permissions-Policy.
    const ta = document.createElement('textarea');
    ta.value = props.sql;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } finally {
      document.body.removeChild(ta);
    }
  };
  return (
    <Modal onClose={props.onClose} minWidth={480} maxWidth={760} maxHeight="80vh" showClose>
      <div style={{ fontSize: '0.9rem' }}>
        <h3 style={{ margin: '0 2rem 0.75rem 0', fontSize: '1rem' }}>What this search matches</h3>
        {props.explanation ? (
          <p style={{ margin: '0 0 1rem', lineHeight: 1.5 }}>{props.explanation}</p>
        ) : (
          <p style={{ margin: '0 0 1rem', color: 'var(--rv-muted)', fontStyle: 'italic' }}>
            No plain-language explanation was attached to this search.
          </p>
        )}
        {matchesText && (
          <p
            style={{
              margin: '0 0 1rem',
              padding: '0.5rem 0.7rem',
              background: 'var(--rv-accent-soft)',
              borderLeft: '3px solid var(--rv-accent)',
              borderRadius: 3,
              color: 'var(--rv-fg)',
              fontSize: '0.78rem',
              lineHeight: 1.45,
            }}
          >
            <strong>Text matching is approximate.</strong> Rows were picked by matching words in the
            report text, so unusual phrasing can be missed and a mention meant to be ruled out can
            slip through. A language model writes these patterns for each search, so be specific
            about what you want, and expect the cohort to change slightly if you ask again or
            rephrase. The SQL below is what defines <em>this</em> cohort. Expand rows in the table
            to review the matches.
          </p>
        )}
        <div style={{ fontWeight: 600, marginBottom: '0.35rem', fontSize: '0.85rem' }}>SQL</div>
        <div style={{ position: 'relative' }}>
          <pre
            style={{
              background: 'var(--rv-surface-2)',
              border: '1px solid var(--rv-border)',
              borderRadius: 3,
              padding: '0.6rem 0.75rem',
              paddingRight: '2.25rem',
              fontSize: '0.74rem',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              // The model may emit the whole statement on one line, which turns a
              // `pre` block into a single unreadable horizontal scroll. Wrap, and
              // break inside long regex literals that have no spaces to wrap at.
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              overflowX: 'auto',
              maxHeight: '18rem',
              overflowY: 'auto',
              margin: 0,
            }}
          >
            {props.sql || '(no SQL recorded)'}
          </pre>
          <button
            type="button"
            onClick={onCopySql}
            disabled={!props.sql}
            title={props.sql ? 'Copy SQL to clipboard' : 'No SQL to copy'}
            aria-label={copied ? 'SQL copied' : 'Copy SQL'}
            style={{
              position: 'absolute',
              top: 5,
              right: 5,
              width: 26,
              height: 26,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 0,
              border: '1px solid transparent',
              background: 'transparent',
              borderRadius: 3,
              cursor: props.sql ? 'pointer' : 'not-allowed',
              color: copied ? 'var(--rv-success)' : 'var(--rv-muted)',
              opacity: props.sql ? 1 : 0.4,
            }}
            onMouseEnter={(e) => {
              if (!props.sql) return;
              e.currentTarget.style.background = 'var(--rv-surface-2)';
              e.currentTarget.style.borderColor = 'var(--rv-border)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.borderColor = 'transparent';
            }}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
          </button>
        </div>
        {props.searchId && <MatchedTextPanel searchId={props.searchId} />}
        {(terms.length > 0 || codes.length > 0) && (
          <div style={{ marginTop: '1rem' }}>
            <div style={{ fontWeight: 600, marginBottom: '0.2rem', fontSize: '0.85rem' }}>
              Match criteria
            </div>
            <p
              style={{
                margin: '0 0 0.5rem',
                color: 'var(--rv-muted)',
                fontSize: '0.78rem',
                lineHeight: 1.4,
              }}
            >
              Words and diagnosis codes the LLM flagged as positive signals. They are highlighted in
              the report text and diagnosis chips when you expand a row, so you can spot-check why
              each row matched. <strong>Display only:</strong> these do not filter the search, the
              SQL above is what selected these rows.
            </p>
            {terms.length > 0 && (
              <div style={{ marginBottom: codes.length > 0 ? '0.4rem' : 0 }}>
                <span
                  style={{ color: 'var(--rv-muted)', fontSize: '0.78rem', marginRight: '0.4rem' }}
                >
                  Match terms:
                </span>
                {terms.map((t, i) => (
                  <code
                    key={i}
                    style={{
                      background: '#fff3a3',
                      color: '#222',
                      padding: '0 4px',
                      marginRight: 4,
                      borderRadius: 2,
                      fontSize: '0.78rem',
                    }}
                  >
                    {t}
                  </code>
                ))}
              </div>
            )}
            {codes.length > 0 && (
              <div>
                <span
                  style={{ color: 'var(--rv-muted)', fontSize: '0.78rem', marginRight: '0.4rem' }}
                >
                  Match diagnoses:
                </span>
                {codes.map((d, i) => (
                  <code
                    key={i}
                    style={{
                      background: '#fff3a3',
                      color: '#222',
                      padding: '0 4px',
                      marginRight: 4,
                      borderRadius: 2,
                      fontSize: '0.78rem',
                    }}
                  >
                    {d}
                  </code>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

// EXPERIMENTAL / WIP: what the search actually matched, ranked.
// A term list cannot be compared across searches -- it drops the structure and is
// the model's summary rather than the query's effect. This is the effect: same
// shape every time, so two cohorts can be read side by side. The tail is the point;
// coincidental matches surface there, not in the head.
function MatchedTextPanel(props: { searchId: string }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ['spans', props.searchId],
    queryFn: () => getMatchedSpans(props.searchId),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  const spans = q.data?.spans ?? [];
  const repeated = spans.filter((s) => s.n > 1);
  const singles = spans.filter((s) => s.n === 1);
  return (
    <div style={{ marginTop: '1rem' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: 'transparent',
          border: 'none',
          padding: 0,
          font: 'inherit',
          fontWeight: 600,
          fontSize: '0.85rem',
          color: 'var(--rv-accent)',
          cursor: 'pointer',
        }}
      >
        {open ? '▾' : '▸'} What this search matched
      </button>
      {open && (
        <div style={{ marginTop: '0.5rem', fontSize: '0.78rem' }}>
          {q.isLoading && <span style={{ color: 'var(--rv-muted)' }}>Counting…</span>}
          {q.isError && (
            <span style={{ color: 'var(--rv-muted)' }}>Could not load the breakdown.</span>
          )}
          {q.data && !q.data.supported && (
            <span style={{ color: 'var(--rv-muted)' }}>
              This search has no free-text pattern to break down.
            </span>
          )}
          {q.data?.supported && (
            <>
              <p style={{ margin: '0 0 0.5rem', color: 'var(--rv-muted)', lineHeight: 1.4 }}>
                The distinct phrases the pattern matched, most common first — {q.data.distinct}{' '}
                phrasings across {q.data.total} rows. Compare this between searches to see what a
                reworded question changed.
              </p>
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <tbody>
                  {repeated.map((s, i) => (
                    <tr key={i}>
                      <td
                        style={{
                          padding: '1px 0',
                          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                        }}
                      >
                        {s.text}
                      </td>
                      <td
                        style={{
                          padding: '1px 0 1px 0.75rem',
                          textAlign: 'right',
                          fontVariantNumeric: 'tabular-nums',
                          color: 'var(--rv-muted)',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {s.n}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {singles.length > 0 && (
                <details style={{ marginTop: '0.5rem' }}>
                  <summary style={{ cursor: 'pointer', color: 'var(--rv-muted)' }}>
                    {singles.length} phrasings seen once
                  </summary>
                  <div
                    style={{
                      marginTop: '0.35rem',
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      lineHeight: 1.5,
                    }}
                  >
                    {singles.map((s, i) => (
                      <div key={i}>{s.text}</div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Octicons copy / check (16px viewBox, MIT).
function CopyIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25Z" />
      <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.751.751 0 0 1 .018-1.042.751.751 0 0 1 1.042-.018L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z" />
    </svg>
  );
}
