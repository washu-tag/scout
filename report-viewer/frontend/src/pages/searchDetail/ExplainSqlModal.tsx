import { useState } from 'react';
import { Modal } from '../../Modal';
import { CheckIcon, CopyIcon } from './icons';

export function ExplainSqlModal(props: {
  explanation: string;
  sql: string;
  highlightTerms: string[];
  highlightDiagnosis: string[];
  onClose: () => void;
}) {
  const terms = props.highlightTerms.filter((t) => t.trim().length > 0);
  // Gate on the SQL: match_terms is a model-supplied hint it can omit.
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
            <strong>Text matching is approximate.</strong> Reports were picked by matching words in
            the report text, so unusual phrasing can be missed and a mention meant to be ruled out
            can slip through. A language model writes these patterns, so be specific about what you
            want, and expect results to shift a little if you ask again. The SQL below is what did
            the matching.
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
              // The model may emit the whole statement on one line; wrap, and break inside long regexes.
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
