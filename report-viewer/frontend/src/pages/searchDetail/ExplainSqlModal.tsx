import { useState } from 'react';
import type { FilterState } from '../../api/client';
import { Modal } from '../../Modal';
import { MatchStats } from './MatchStats';
import { hasEvidence } from './evidenceStats';

export function ExplainSqlModal(props: {
  explanation: string;
  sql: string;
  executedSql?: string;
  rows: Record<string, unknown>[];
  onFilter?: (patch: Partial<FilterState>) => void;
  onClose: () => void;
}) {
  const showStats = hasEvidence(props.rows);
  // Gate on the SQL: match_terms is a model-supplied hint it can omit.
  const matchesText = /REGEXP_LIKE/i.test(props.sql);
  const [copied, setCopied] = useState('');
  const copy = (label: string, text: string) => () => {
    if (!text) return;
    // execCommand is deprecated but unavoidable: navigator.clipboard is
    // blocked by OWUI's artifact-iframe Permissions-Policy.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      setCopied(label);
      setTimeout(() => setCopied(''), 1500);
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
            want, and expect results to shift a little if you ask again.
          </p>
        )}
        {showStats && <MatchStats rows={props.rows} onFilter={props.onFilter} />}
        <SqlSection
          label="LLM generated SQL"
          sql={props.sql || '(no SQL recorded)'}
          copied={copied === 'assistant'}
          onCopy={copy('assistant', props.sql)}
        />
        {/* Recomputed per request, so it is only what ran when the rows came
            back scored; a rewrite Trino rejected falls back to the SQL above. */}
        {showStats && props.executedSql && (
          <SqlSection
            label="Evaluated SQL"
            note="The LLM generated query with columns added to show why each report matched. It returns the same reports and adds the Matched on evidence."
            sql={props.executedSql}
            copied={copied === 'executed'}
            onCopy={copy('executed', props.executedSql)}
          />
        )}
      </div>
    </Modal>
  );
}

function SqlSection(props: {
  label: string;
  note?: string;
  sql: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <details style={{ marginTop: '0.75rem' }}>
      <summary
        style={{
          cursor: 'pointer',
          fontWeight: 600,
          fontSize: '0.85rem',
          marginBottom: '0.35rem',
        }}
      >
        {props.label}
      </summary>
      {props.note && (
        <p style={{ margin: '0 0 0.4rem', color: 'var(--rv-muted)', fontSize: '0.75rem' }}>
          {props.note}
        </p>
      )}
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
          {props.sql}
        </pre>
        <button
          type="button"
          onClick={props.onCopy}
          title="Copy SQL to clipboard"
          aria-label={props.copied ? 'SQL copied' : 'Copy SQL'}
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
            cursor: 'pointer',
            color: props.copied ? 'var(--rv-success)' : 'var(--rv-muted)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--rv-surface)';
            e.currentTarget.style.borderColor = 'var(--rv-border)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.borderColor = 'transparent';
          }}
        >
          {props.copied ? <CheckIcon /> : <CopyIcon />}
        </button>
      </div>
    </details>
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
