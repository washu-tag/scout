import { useState } from 'react';
import { Modal } from '../../Modal';

export function ExplainSqlModal(props: {
  explanation: string;
  sql: string;
  highlightTerms: string[];
  highlightDiagnosis: string[];
  onClose: () => void;
}) {
  const terms = props.highlightTerms.filter((t) => t.trim().length > 0);
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
    <Modal onClose={props.onClose} minWidth={480} maxWidth={760} maxHeight="80vh">
      <div style={{ fontSize: '0.9rem' }}>
        <button
          type="button"
          onClick={props.onClose}
          aria-label="Close"
          title="Close"
          style={{
            position: 'absolute',
            top: 10,
            right: 10,
            width: 28,
            height: 28,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 0,
            border: '1px solid transparent',
            background: 'transparent',
            borderRadius: 3,
            cursor: 'pointer',
            color: '#5a6b80',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = '#e6edf6';
            e.currentTarget.style.borderColor = '#d0dceb';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.borderColor = 'transparent';
          }}
        >
          <CloseIcon />
        </button>
        <h3 style={{ margin: '0 2rem 0.75rem 0', fontSize: '1rem' }}>What this search matches</h3>
        {props.explanation ? (
          <p style={{ margin: '0 0 1rem', lineHeight: 1.5 }}>{props.explanation}</p>
        ) : (
          <p style={{ margin: '0 0 1rem', color: '#888', fontStyle: 'italic' }}>
            No plain-language explanation was attached to this search (older searches, or the model
            didn&apos;t supply one).
          </p>
        )}
        <div style={{ fontWeight: 600, marginBottom: '0.35rem', fontSize: '0.85rem' }}>SQL</div>
        <div style={{ position: 'relative' }}>
          <pre
            style={{
              background: '#f4f7fb',
              border: '1px solid #d0dceb',
              borderRadius: 3,
              padding: '0.6rem 0.75rem',
              paddingRight: '2.25rem',
              fontSize: '0.74rem',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              whiteSpace: 'pre',
              overflowX: 'auto',
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
              color: copied ? '#1a7a3a' : '#5a6b80',
              opacity: props.sql ? 1 : 0.4,
            }}
            onMouseEnter={(e) => {
              if (!props.sql) return;
              e.currentTarget.style.background = '#e6edf6';
              e.currentTarget.style.borderColor = '#d0dceb';
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
              style={{ margin: '0 0 0.5rem', color: '#666', fontSize: '0.78rem', lineHeight: 1.4 }}
            >
              Words and diagnosis codes the LLM flagged as positive signals. They are highlighted in
              the report text and diagnosis chips when you expand a row, so you can spot-check why
              each row matched. <strong>Display only:</strong> these do not filter the search, the
              SQL above is what selected these rows.
            </p>
            {terms.length > 0 && (
              <div style={{ marginBottom: codes.length > 0 ? '0.4rem' : 0 }}>
                <span style={{ color: '#666', fontSize: '0.78rem', marginRight: '0.4rem' }}>
                  Match terms:
                </span>
                {terms.map((t, i) => (
                  <code
                    key={i}
                    style={{
                      background: '#fff3a3',
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
                <span style={{ color: '#666', fontSize: '0.78rem', marginRight: '0.4rem' }}>
                  Match diagnoses:
                </span>
                {codes.map((d, i) => (
                  <code
                    key={i}
                    style={{
                      background: '#fff3a3',
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

// Octicons copy / check / close (16px viewBox, MIT).
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

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.749.749 0 0 1 1.275.326.749.749 0 0 1-.215.734L9.06 8l3.22 3.22a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215L8 9.06l-3.22 3.22a.751.751 0 0 1-1.042-.018.751.751 0 0 1-.018-1.042L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
    </svg>
  );
}
