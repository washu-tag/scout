import { useEffect, useRef } from 'react';

export function Modal(props: {
  onClose: () => void;
  ariaLabel?: string;
  minWidth?: number;
  maxWidth?: number;
  maxHeight?: string;
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
        {props.children}
      </div>
    </div>
  );
}
