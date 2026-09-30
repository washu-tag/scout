import { useCallback, useEffect } from 'react';
import { RowDetail } from './RowDetail';
import { paginationBtn } from './styles';

type Row = Record<string, unknown>;

/** Sequential reader over a queue of reports, for review and comparison. */
export function ReviewPanel(props: {
  queue: Row[];
  index: number;
  selectedCount: number;
  isSelected: boolean;
  onIndex: (next: number) => void;
  onToggleSelect: () => void;
  onClose: () => void;
}) {
  const { queue, index, onIndex, onToggleSelect, onClose } = props;
  const row = queue[index];

  const step = useCallback(
    (delta: number) => onIndex(Math.min(queue.length - 1, Math.max(0, index + delta))),
    [index, queue.length, onIndex],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowDown' || e.key === 'j') step(1);
      else if (e.key === 'ArrowUp' || e.key === 'k') step(-1);
      else if (e.key === 'x') onToggleSelect();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, onClose, onToggleSelect]);

  if (!row) return null;

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.25)', zIndex: 20 }}
      />
      <aside
        aria-label="Report review"
        style={{
          position: 'absolute',
          top: 0,
          right: 0,
          bottom: 0,
          width: 'clamp(320px, 75%, 900px)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--rv-bg)',
          borderLeft: '1px solid var(--rv-border)',
          boxShadow: '-4px 0 16px rgba(0,0,0,0.18)',
          zIndex: 21,
        }}
      >
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
            padding: '0.4rem 0.6rem',
            borderBottom: '1px solid var(--rv-border)',
            background: 'var(--rv-surface)',
            flex: '0 0 auto',
            fontSize: '0.78rem',
          }}
        >
          <button
            type="button"
            onClick={() => step(-1)}
            disabled={index === 0}
            style={paginationBtn}
            title="Previous report (k)"
          >
            ‹
          </button>
          <span style={{ fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
            {index + 1} of {queue.length.toLocaleString()}
          </span>
          <button
            type="button"
            onClick={() => step(1)}
            disabled={index >= queue.length - 1}
            style={paginationBtn}
            title="Next report (j)"
          >
            ›
          </button>
          <label
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
              cursor: 'pointer',
              marginLeft: '0.4rem',
              whiteSpace: 'nowrap',
            }}
            title="Select this report (x)"
          >
            <input type="checkbox" checked={props.isSelected} onChange={onToggleSelect} />
            {props.selectedCount > 0 ? `${props.selectedCount} selected` : 'select'}
          </label>
          <span style={{ flex: 1 }} />
          <button type="button" onClick={onClose} style={paginationBtn} title="Close (Esc)">
            Close
          </button>
        </header>
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '0.6rem 0.8rem' }}>
          <RowDetail row={row} wide />
        </div>
      </aside>
    </>
  );
}
