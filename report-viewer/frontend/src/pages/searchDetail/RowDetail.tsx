import { Fragment, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { friendlyError, getReport } from '../../api/client';
import { buildDiscussPrompt } from '../../chat';
import { useChatPrompt } from '../../ChatPrompt';
import { fmtDate } from './format';
import { paginationBtn } from './styles';

export function RowDetail(props: {
  row: Record<string, unknown>;
  idColumn: string;
  highlightTerms: string[];
  highlightDiagnosis: string[];
}) {
  const requestPrompt = useChatPrompt();
  const reportId = String(props.row[props.idColumn] ?? '');
  const reportQ = useQuery({
    queryKey: ['report', props.idColumn, reportId],
    queryFn: () => getReport(reportId, props.idColumn),
    enabled: !!reportId,
    staleTime: 5 * 60_000,
  });

  // \b boundaries so short tokens like "PE" don't match in "pectoralis".
  const escaped = props.highlightTerms
    .map((t) => t.trim())
    .filter((t) => t.length >= 2)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  // safe: all regex metachars escaped above, alternation of literal strings is linear-time
  // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp
  const highlightRe = escaped.length ? new RegExp(`\\b(${escaped.join('|')})\\b`, 'gi') : null;

  // Strip SQL-LIKE `%` so the LLM can pass `R91` or `R91%` - same thing.
  const dxPrefixes = props.highlightDiagnosis
    .map((d) => d.trim().replace(/%+$/, '').toLowerCase())
    .filter((d) => d.length >= 1);

  const applyTextHighlights = (text: string): ReactNode => {
    if (!text) return null;
    if (!highlightRe) return text;
    const parts = text.split(highlightRe);
    return parts.map((p, i) =>
      i % 2 === 1 ? (
        <mark key={i} style={{ background: '#fff3a3', color: '#222', padding: '0 1px' }}>
          {p}
        </mark>
      ) : (
        <Fragment key={i}>{p}</Fragment>
      ),
    );
  };

  const diagnoses = reportQ.data?.diagnoses ?? props.row.diagnoses;
  const meta = reportQ.data ?? props.row;
  const fmt = (v: unknown): string => {
    if (v === null || v === undefined) return '-';
    const s = String(v);
    return s.length > 0 ? s : '-';
  };
  const fmtPerson = (v: unknown): string => {
    if (!v) return '-';
    if (Array.isArray(v)) return v.length ? v.join(', ') : '-';
    return String(v);
  };

  const dxList = Array.isArray(diagnoses) ? (diagnoses as Array<Record<string, unknown>>) : [];
  const positiveDxIndex = new Set<number>();
  for (let i = 0; i < dxList.length; i++) {
    const code = String(dxList[i].diagnosis_code ?? '');
    const text = String(dxList[i].diagnosis_code_text ?? '');
    if (!code) continue;
    const codeLc = code.toLowerCase();
    if (dxPrefixes.some((p) => codeLc.startsWith(p))) {
      positiveDxIndex.add(i);
      continue;
    }
    if (highlightRe) {
      // Reset lastIndex; it sticks across .test() calls on /g regexes.
      highlightRe.lastIndex = 0;
      if (highlightRe.test(code + ' ' + text)) positiveDxIndex.add(i);
    }
  }

  const m = meta as Partial<{
    race: unknown;
    ethnic_group: unknown;
    birth_date: unknown;
    requested_dt: unknown;
    observation_dt: unknown;
    results_report_status_change_dt: unknown;
    report_status: unknown;
    study_instance_uid: unknown;
    principal_result_interpreter: unknown;
    assistant_result_interpreter: unknown;
    technician: unknown;
    diagnostic_service_id: unknown;
  }> &
    Record<string, unknown>;

  return (
    <div style={{ fontSize: '0.78rem', lineHeight: 1.4, color: 'var(--rv-fg)' }}>
      <div
        style={{
          padding: '0.5rem 0.7rem',
          marginBottom: '0.5rem',
          background: 'var(--rv-surface)',
          border: '1px solid var(--rv-border)',
          borderRadius: 4,
          fontSize: '0.74rem',
          boxShadow: '0 1px 4px rgba(31, 95, 168, 0.10)',
        }}
      >
        <CardRow>
          <CardField label="MRN" value={fmt(meta.resolved_epic_mrn)} mono />
          <CardField label="Accession" value={fmt(meta.accession_number)} mono />
          <CardField label="Age" value={fmt(meta.patient_age)} />
          <CardField label="Sex" value={fmt(meta.sex)} />
          {m.race ? <CardField label="Race" value={fmt(m.race)} /> : null}
          {m.ethnic_group ? <CardField label="Ethnic" value={fmt(m.ethnic_group)} /> : null}
        </CardRow>
        <CardRow>
          <CardField label="Modality" value={fmt(meta.modality)} />
          <CardField label="Service" value={fmt(meta.service_name)} />
          <CardField label="Facility" value={fmt(meta.sending_facility)} />
          {m.report_status ? <CardField label="Status" value={fmt(m.report_status)} /> : null}
        </CardRow>
        <CardRow>
          <CardField label="Requested" value={fmtDate(m.requested_dt)} />
          <CardField label="Observed" value={fmtDate(m.observation_dt)} />
          <CardField label="Reported" value={fmtDate(meta.message_dt)} />
        </CardRow>
        {m.principal_result_interpreter || m.assistant_result_interpreter || m.technician ? (
          <CardRow>
            <CardField label="Interpreter" value={fmtPerson(m.principal_result_interpreter)} />
            {m.assistant_result_interpreter ? (
              <CardField label="Assistant" value={fmtPerson(m.assistant_result_interpreter)} />
            ) : null}
            {m.technician ? <CardField label="Technician" value={fmtPerson(m.technician)} /> : null}
          </CardRow>
        ) : null}
      </div>

      {dxList.length > 0 && (
        <div style={{ marginBottom: '0.5rem' }}>
          <div
            style={{
              fontSize: '0.7rem',
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
              color: 'var(--rv-muted)',
              marginBottom: '0.2rem',
            }}
          >
            Diagnoses ({dxList.length})
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
            {dxList.map((d, i) => {
              const positive = positiveDxIndex.has(i);
              return (
                <span
                  key={i}
                  title={String(d.diagnosis_code_text ?? '')}
                  style={{
                    display: 'inline-flex',
                    gap: '0.35rem',
                    padding: '0.15rem 0.4rem',
                    borderRadius: 3,
                    background: positive ? '#fff3a3' : 'var(--rv-surface-2)',
                    border: positive ? '1px solid #d6b500' : '1px solid var(--rv-border)',
                    fontSize: '0.72rem',
                    color: positive ? '#222' : 'var(--rv-fg)',
                    fontWeight: positive ? 600 : 400,
                  }}
                >
                  <code
                    style={{
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      color: positive ? '#7a5a00' : 'var(--rv-accent)',
                    }}
                  >
                    {String(d.diagnosis_code ?? '')}
                  </code>
                  <span style={{ wordBreak: 'break-word' }}>
                    {String(d.diagnosis_code_text ?? '')}
                  </span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {reportQ.isLoading && <div style={{ color: 'var(--rv-muted)' }}>Loading report…</div>}
      {reportQ.error && (
        <div style={{ color: 'var(--rv-danger)' }}>
          {friendlyError(reportQ.error, 'this report')}
        </div>
      )}
      {reportQ.data && (
        <div
          style={{
            whiteSpace: 'pre-wrap',
            color: 'var(--rv-fg)',
            background: 'var(--rv-surface)',
            border: '1px solid var(--rv-border)',
            borderRadius: 3,
            padding: '0.4rem 0.6rem',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontSize: '0.74rem',
          }}
        >
          {applyTextHighlights(
            (reportQ.data.report_text as string | null) ??
              (reportQ.data.report_section_impression as string | null) ??
              (reportQ.data.report_section_findings as string | null) ??
              '',
          ) || <em style={{ color: 'var(--rv-muted)' }}>(empty)</em>}
        </div>
      )}

      {(() => {
        const sourceFile =
          (reportQ.data?.source_file as string | undefined) ??
          (props.row.source_file as string | undefined);
        if (!sourceFile) return null;
        return (
          <div
            style={{
              marginTop: '0.5rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
            }}
          >
            <div
              title={sourceFile}
              style={{
                flex: 1,
                minWidth: 0,
                color: 'var(--rv-muted)',
                fontSize: '0.7rem',
                display: 'flex',
                alignItems: 'baseline',
                gap: '0.35rem',
              }}
            >
              <span style={{ fontWeight: 600, flexShrink: 0 }}>Lake path:</span>
              <span
                style={{
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  userSelect: 'all',
                  minWidth: 0,
                  flex: 1,
                }}
              >
                {sourceFile}
              </span>
            </div>
            <button
              type="button"
              onClick={() =>
                requestPrompt(buildDiscussPrompt(sourceFile), { title: 'Discuss in Chat?' })
              }
              style={paginationBtn}
            >
              Discuss in Chat
            </button>
          </div>
        );
      })()}
    </div>
  );
}

function CardRow(props: { children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.15rem 0.7rem',
        marginBottom: '0.25rem',
      }}
    >
      {props.children}
    </div>
  );
}

function CardField(props: { label: string; value: string; mono?: boolean }) {
  // Wrap rather than truncate so long service names stay legible.
  return (
    <span
      style={{
        display: 'inline-flex',
        gap: '0.3rem',
        flexWrap: 'wrap',
        alignItems: 'baseline',
      }}
    >
      <span style={{ color: 'var(--rv-muted)', fontWeight: 600 }}>{props.label}</span>
      <span
        style={{
          color: 'var(--rv-fg)',
          fontFamily: props.mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : 'inherit',
          wordBreak: 'break-word',
        }}
        title={props.value}
      >
        {props.value}
      </span>
    </span>
  );
}
