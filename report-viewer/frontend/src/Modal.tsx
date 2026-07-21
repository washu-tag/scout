import { useEffect, useRef } from 'react';

export function Modal(props: {
  onClose: () => void;
  ariaLabel?: string;
  minWidth?: number;
  maxWidth?: number;
  maxHeight?: string;
  showClose?: boolean;
  children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') props.onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [props.onClose]);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  return (
    <div
      onClick={props.onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={props.ariaLabel}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'relative',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
          background: 'var(--rv-surface)',
          color: 'var(--rv-fg)',
          padding: '1.25rem 1.5rem',
          borderRadius: 6,
          minWidth: props.minWidth,
          maxWidth: props.maxWidth,
          maxHeight: props.maxHeight,
          overflowY: props.maxHeight ? 'auto' : undefined,
          boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
          outline: 'none',
        }}
      >
        {props.showClose && (
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
              color: 'var(--rv-muted)',
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
            <CloseIcon />
          </button>
        )}
        {props.children}
      </div>
    </div>
  );
}

// Octicon x (16px viewBox, MIT).
function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.749.749 0 0 1 1.275.326.749.749 0 0 1-.215.734L9.06 8l3.22 3.22a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215L8 9.06l-3.22 3.22a.751.751 0 0 1-1.042-.018.751.751 0 0 1-.018-1.042L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
    </svg>
  );
}
