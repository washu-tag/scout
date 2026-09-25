import { Modal } from '../../Modal';
import { CheckIcon, CopyIcon } from './icons';

/** Shown after an `open-url`/`backend-call` action's popup attempt, so the
 * link doesn't linger indefinitely as a persistent block of text in the
 * toolbar (team feedback from the #739 demo) - same dismissible-box
 * machinery as ExplainSqlModal, closed via the X, Escape, or a backdrop
 * click. `onCopy` must be called synchronously from this modal's own
 * button click - see openResult.ts's doc comment on why `copyLink` can't
 * be deferred behind an await. */
export function ResultLinkModal(props: {
  url: string;
  copied: boolean;
  onCopy: () => void;
  onClose: () => void;
}) {
  return (
    <Modal onClose={props.onClose} minWidth={360} maxWidth={640} showClose ariaLabel="Result link">
      <div style={{ fontSize: '0.9rem' }}>
        <h3 style={{ margin: '0 2rem 0.75rem 0', fontSize: '1rem' }}>Opened in a new tab</h3>
        <p style={{ margin: '0 0 0.75rem', color: 'var(--rv-muted)', lineHeight: 1.5 }}>
          If it didn't open, copy this link and open it manually:
        </p>
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
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              margin: 0,
            }}
          >
            {props.url}
          </pre>
          <button
            type="button"
            onClick={props.onCopy}
            title="Copy link to clipboard"
            aria-label={props.copied ? 'Link copied' : 'Copy link'}
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
              e.currentTarget.style.background = 'var(--rv-surface-2)';
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
      </div>
    </Modal>
  );
}
