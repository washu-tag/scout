import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import type { QueryProgress } from './api/client';

const POLL_MS = 1000;

// An empty or failed poll keeps the last value; resetting made the bar flicker.
export function useQueryProgress(
  active: boolean,
  fetchProgress: () => Promise<QueryProgress>,
): QueryProgress | null {
  const [progress, setProgress] = useState<QueryProgress | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const p = await fetchProgress();
        if (!cancelled && Object.keys(p).length) setProgress(p);
      } catch {
        /* keep the last value */
      }
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active, fetchProgress]);

  return progress;
}

function compactNumber(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return String(n);
}

function compactBytes(b: number): string {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(0)} MB`;
  return `${(b / 1e3).toFixed(0)} KB`;
}

const STATE_LABELS: Record<string, string> = {
  QUEUED: 'Waiting for cluster capacity',
  PLANNING: 'Planning the query',
  STARTING: 'Starting up',
  RUNNING: 'Scanning reports',
  FINISHING: 'Fetching results',
  FINISHED: 'Fetching results',
};

function stateLine(p: QueryProgress | null): string {
  if (!p) return 'Starting up';
  if (p.queued) return STATE_LABELS.QUEUED;
  return (p.state && STATE_LABELS[p.state]) || 'Working';
}

function detailLine(p: QueryProgress | null, waitedSeconds: number): string {
  const parts: string[] = [];
  if (p?.progressPercentage != null) parts.push(`${p.progressPercentage.toFixed(0)}%`);
  if (p?.processedRows) parts.push(`${compactNumber(p.processedRows)} rows`);
  if (p?.processedBytes) parts.push(compactBytes(p.processedBytes));
  parts.push(`${waitedSeconds}s`);
  return parts.join(' · ');
}

// The user's whole wait, not Trino's `elapsedTimeMillis`, which starts later.
function useWaitedSeconds(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);
  return seconds;
}

// Percentage is unclamped on purpose: Trino's goes backwards as it finds splits.
export function QueryProgressModal({
  label,
  progress,
}: {
  label: string;
  progress: QueryProgress | null;
}) {
  const pct = progress?.progressPercentage;
  const waited = useWaitedSeconds();

  return (
    <Modal onClose={() => {}} ariaLabel={label} minWidth={260} maxWidth={320}>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.6rem',
          alignItems: 'center',
          textAlign: 'center',
        }}
      >
        <div style={{ fontSize: '0.85rem' }}>{label}</div>
        <div style={{ fontSize: '0.72rem', color: 'var(--rv-muted)' }}>{stateLine(progress)}</div>
        <div
          style={{
            width: '100%',
            height: 4,
            borderRadius: 2,
            background: 'var(--rv-surface-2)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              height: '100%',
              borderRadius: 2,
              background: 'var(--rv-accent)',
              width: pct == null ? '100%' : `${Math.min(100, Math.max(0, pct))}%`,
              opacity: pct == null ? 0.35 : 1,
              animation: pct == null ? 'rvPulse 1.6s ease-in-out infinite' : undefined,
              transition: 'width 0.4s ease',
            }}
          />
        </div>
        <div
          style={{
            fontSize: '0.7rem',
            color: 'var(--rv-muted)',
            fontVariantNumeric: 'tabular-nums',
            minHeight: '1em',
          }}
        >
          {detailLine(progress, waited)}
        </div>
      </div>
    </Modal>
  );
}
