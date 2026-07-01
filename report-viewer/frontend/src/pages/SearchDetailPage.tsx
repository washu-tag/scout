import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  useReactTable,
  type SortingState,
  type ExpandedState,
  type VisibilityState,
} from '@tanstack/react-table';
import {
  activeFilterCount,
  friendlyError,
  getSearch,
  getSearchRows,
  getReport,
  type FilterState,
} from '../api/client';
import { HEIGHT_COMPACT, HEIGHT_EXPANDED, setHeight as setIframeHeight } from '../iframeHeight';
import { chatOrigin } from '../chat';
import { Modal } from '../Modal';

const ROW_ACTIVE_BG = '#e8f0fa';
const DETAIL_ZONE_BG = '#f0f6fc';

const COLUMNS_CONFIG: Array<{
  field: string;
  title: string;
  width: number;
  defaultHidden?: boolean;
  align?: 'right' | 'center';
  mono?: boolean;
  kind?: 'date';
}> = [
  { field: 'accession_number', title: 'Accession', width: 100, mono: true },
  { field: 'epic_mrn', title: 'MRN', width: 80, mono: true },
  { field: 'message_dt', title: 'Date', width: 100, kind: 'date' },
  { field: 'modality', title: 'Modality', width: 60 },
  { field: 'service_name', title: 'Service', width: 180 },
  { field: 'sending_facility', title: 'Facility', width: 120, defaultHidden: true },
  { field: 'patient_age', title: 'Age', width: 90, align: 'right' },
  { field: 'sex', title: 'Sex', width: 40, align: 'center' },
  { field: 'evidence', title: 'Label', width: 110, defaultHidden: true },
];

type Row = Record<string, unknown>;

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

// Trino returns `timestamp with time zone` as `YYYY-MM-DDTHH:MM:SS+00:00`
// (everything in the lake is UTC). Trim seconds and TZ offset for the UI.
function fmtDate(v: unknown): string {
  if (!v) return '-';
  const d = new Date(String(v));
  if (isNaN(d.getTime())) return String(v);
  const iso = d.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}

const columnHelper = createColumnHelper<Row>();

export default function SearchDetailPage() {
  const { searchId = '' } = useParams<{ searchId: string }>();
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(100);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [appliedFilters, setAppliedFilters] = useState<FilterState>({});
  const [filtersModalOpen, setFiltersModalOpen] = useState(false);
  const [xnatModalOpen, setXnatModalOpen] = useState(false);
  const [sqlModalOpen, setSqlModalOpen] = useState(false);
  const [colPickerOpen, setColPickerOpen] = useState(false);
  const colPickerRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() =>
    Object.fromEntries(COLUMNS_CONFIG.filter((c) => c.defaultHidden).map((c) => [c.field, false])),
  );
  const [iframeExpanded, setIframeExpanded] = useState(false);
  const appliedFiltersKey = useMemo(() => JSON.stringify(appliedFilters), [appliedFilters]);

  const meta = useQuery({
    queryKey: ['search', searchId],
    queryFn: () => getSearch(searchId),
    enabled: !!searchId,
  });

  const sortParam = sorting[0]
    ? { col: sorting[0].id, dir: (sorting[0].desc ? 'desc' : 'asc') as 'asc' | 'desc' }
    : null;
  const rowsQ = useQuery({
    queryKey: ['search', searchId, 'rows', page, limit, sortParam, appliedFiltersKey],
    queryFn: () =>
      getSearchRows(searchId, { page, limit, sort: sortParam, filters: appliedFilters }),
    enabled: !!searchId,
    // Keep previous page visible during refetch so debounced filter inputs don't lose focus.
    placeholderData: keepPreviousData,
  });

  // Expansion is keyed by row index; clear on data change so page-2 row 0
  // doesn't inherit page-1's expanded card. Gate on rowsQ.data (not page)
  // to avoid a mid-fetch collapse flash under keepPreviousData.
  useEffect(() => {
    setExpanded({});
  }, [rowsQ.data]);

  useEffect(() => {
    if (!colPickerOpen) return;
    const onEvent = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent && e.key !== 'Escape') return;
      if (e instanceof MouseEvent && colPickerRef.current?.contains(e.target as Node)) return;
      setColPickerOpen(false);
    };
    document.addEventListener('mousedown', onEvent);
    document.addEventListener('keydown', onEvent);
    return () => {
      document.removeEventListener('mousedown', onEvent);
      document.removeEventListener('keydown', onEvent);
    };
  }, [colPickerOpen]);

  const total = rowsQ.data?.total ?? meta.data?.count ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / limit));

  const available = useMemo<string[]>(
    () => rowsQ.data?.columns ?? (rowsQ.data?.rows?.[0] ? Object.keys(rowsQ.data.rows[0]) : []),
    [rowsQ.data],
  );

  const columns = useMemo(
    () =>
      COLUMNS_CONFIG.filter((c) => available.includes(c.field)).map((c) =>
        columnHelper.accessor((row: Row) => row[c.field], {
          id: c.field,
          header: c.title,
          size: c.width,
          cell: (info) => (c.kind === 'date' ? fmtDate(info.getValue()) : fmtCell(info.getValue())),
          meta: { align: c.align, mono: c.mono },
        }),
      ),
    [available],
  );

  const data = rowsQ.data?.rows ?? [];

  const table = useReactTable({
    data,
    columns,
    state: { sorting, expanded, columnVisibility },
    onSortingChange: (updater) => {
      setSorting(updater);
      setPage(1);
    },
    onExpandedChange: setExpanded,
    onColumnVisibilityChange: setColumnVisibility,
    getRowCanExpand: () => true,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    manualSorting: true,
    columnResizeMode: 'onChange',
    defaultColumn: { minSize: 40 },
  });

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: '1 1 auto',
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          marginBottom: '0.3rem',
          fontSize: '0.85rem',
          flex: '0 0 auto',
        }}
      >
        <span style={{ flex: 1 }} />
        {rowsQ.data && (
          <span
            title="Search ID"
            style={{
              color: '#999',
              fontSize: '0.7rem',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              userSelect: 'all',
            }}
          >
            {searchId}
          </span>
        )}
      </div>
      {rowsQ.error && <p style={{ color: '#b00' }}>{friendlyError(rowsQ.error, 'these rows')}</p>}
      {!rowsQ.data && rowsQ.isLoading ? (
        <p style={{ color: '#666' }}>Loading rows…</p>
      ) : (
        rowsQ.data && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              flex: '1 1 auto',
              minHeight: 0,
            }}
          >
            <div
              style={{
                overflowX: 'auto',
                overflowY: 'auto',
                flex: '1 1 auto',
                minHeight: 0,
                background: '#fff',
                border: '1px solid #e2e2e2',
                borderRadius: 4,
              }}
            >
              <table
                style={{
                  borderCollapse: 'collapse',
                  fontSize: '0.85rem',
                  width: '100%',
                  // Fixed layout so column-resize widths actually render.
                  tableLayout: 'fixed',
                }}
              >
                <thead>
                  {table.getHeaderGroups().map((hg) => (
                    <tr key={hg.id}>
                      {hg.headers.map((header) => {
                        const colMeta = header.column.columnDef.meta as
                          | { align?: 'right' | 'center' }
                          | undefined;
                        const sorted = header.column.getIsSorted();
                        const isResizing = header.column.getIsResizing();
                        return (
                          <th
                            key={header.id}
                            onClick={header.column.getToggleSortingHandler()}
                            style={{
                              textAlign: colMeta?.align ?? 'left',
                              padding: '0.35rem 0.45rem',
                              fontSize: '0.78rem',
                              fontWeight: 600,
                              color: '#555',
                              background: '#f5f5f5',
                              // border-collapse: collapse + sticky drops
                              // border-bottom on scroll; box-shadow survives.
                              boxShadow: 'inset 0 -1px 0 #c8ccd0',
                              whiteSpace: 'nowrap',
                              width: header.getSize(),
                              cursor: 'pointer',
                              userSelect: 'none',
                              position: 'sticky',
                              top: 0,
                              zIndex: 1,
                            }}
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {sorted === 'asc' ? ' ↑' : sorted === 'desc' ? ' ↓' : ''}
                            <div
                              className="scout-col-resize"
                              onMouseDown={header.getResizeHandler()}
                              onTouchStart={header.getResizeHandler()}
                              onClick={(e) => e.stopPropagation()}
                              style={{
                                position: 'absolute',
                                right: 0,
                                top: 0,
                                bottom: 0,
                                width: 8,
                                cursor: 'col-resize',
                                userSelect: 'none',
                                touchAction: 'none',
                                ...(isResizing ? { borderRight: '2px solid #4477AA' } : {}),
                              }}
                            />
                          </th>
                        );
                      })}
                    </tr>
                  ))}
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => {
                    const isExpanded = row.getIsExpanded();
                    return (
                      <React.Fragment key={row.id}>
                        <tr
                          className={isExpanded ? undefined : 'scout-row'}
                          onClick={(e) => {
                            row.toggleExpanded();
                            // Scroll into view so the expanded panel doesn't
                            // land below the iframe's visible region.
                            if (!isExpanded) {
                              requestAnimationFrame(() => {
                                (e.currentTarget as HTMLElement | null)?.scrollIntoView({
                                  block: 'start',
                                  behavior: 'smooth',
                                });
                              });
                            }
                          }}
                          style={{
                            borderBottom: '1px solid #f0f0f0',
                            cursor: 'pointer',
                            background: isExpanded ? ROW_ACTIVE_BG : 'transparent',
                          }}
                        >
                          {row.getVisibleCells().map((cell) => {
                            const colMeta = cell.column.columnDef.meta as
                              | { align?: 'right' | 'center'; mono?: boolean }
                              | undefined;
                            return (
                              <td
                                key={cell.id}
                                style={{
                                  padding: '0.3rem 0.45rem',
                                  fontSize: '0.78rem',
                                  textAlign: colMeta?.align ?? 'left',
                                  whiteSpace: 'nowrap',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  fontFamily: colMeta?.mono
                                    ? 'ui-monospace, SFMono-Regular, Menlo, monospace'
                                    : 'inherit',
                                }}
                              >
                                {flexRender(cell.column.columnDef.cell, cell.getContext())}
                              </td>
                            );
                          })}
                        </tr>
                        {isExpanded && (
                          <tr style={{ background: DETAIL_ZONE_BG }}>
                            <td colSpan={columns.length} style={{ padding: 0 }}>
                              <div style={{ padding: '0.75rem 1rem' }}>
                                <RowDetail
                                  row={row.original}
                                  idColumn={meta.data?.id_column ?? 'message_control_id'}
                                  highlightTerms={[
                                    ...(meta.data?.match_terms ?? []),
                                    ...(appliedFilters.service_name
                                      ? [appliedFilters.service_name]
                                      : []),
                                  ]}
                                  highlightDiagnosis={meta.data?.match_diagnoses ?? []}
                                />
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div
              style={{
                display: 'flex',
                gap: '0.5rem',
                alignItems: 'center',
                marginTop: '0.75rem',
                fontSize: '0.85rem',
                flex: '0 0 auto',
                flexWrap: 'wrap',
              }}
            >
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                style={paginationBtn}
              >
                Prev
              </button>
              <span style={{ whiteSpace: 'nowrap' }}>
                {page} / {lastPage}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
                disabled={page >= lastPage}
                style={paginationBtn}
              >
                Next
              </button>
              <span style={{ marginLeft: '0.4rem', color: '#888', whiteSpace: 'nowrap' }}>
                Per page:
              </span>
              <select
                value={limit}
                onChange={(e) => {
                  setLimit(Number(e.target.value));
                  setPage(1);
                }}
                style={{ fontSize: '0.85rem' }}
              >
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
              </select>
              <span
                style={{
                  color: '#666',
                  fontSize: '0.75rem',
                  whiteSpace: 'nowrap',
                }}
              >
                {meta.isLoading
                  ? 'Loading…'
                  : meta.error
                    ? 'Failed to load metadata'
                    : `${total.toLocaleString()} rows`}
              </span>
              {/* visibility (not mount) so the row doesn't reflow on fetch. */}
              <span
                aria-label="Loading"
                role="status"
                aria-hidden={!(rowsQ.isFetching && !rowsQ.isLoading)}
                style={{
                  visibility: rowsQ.isFetching && !rowsQ.isLoading ? 'visible' : 'hidden',
                  width: 13,
                  height: 13,
                  borderRadius: '50%',
                  border: '2px solid #fde6c2',
                  borderTopColor: '#ea580c',
                  animation: 'scoutSpin 0.8s linear infinite',
                  display: 'inline-block',
                }}
              />
              <span style={{ flex: 1 }} />
              <button
                type="button"
                onClick={() => setFiltersModalOpen(true)}
                style={
                  activeFilterCount(appliedFilters) > 0
                    ? {
                        ...paginationBtn,
                        background: '#4477AA',
                        color: '#fff',
                        borderColor: '#4477AA',
                      }
                    : paginationBtn
                }
                title="Filter rows"
              >
                {activeFilterCount(appliedFilters) > 0
                  ? `Filters (${activeFilterCount(appliedFilters)})`
                  : 'Filters'}
              </button>
              <div ref={colPickerRef} style={{ position: 'relative' }}>
                <button
                  type="button"
                  onClick={() => setColPickerOpen((v) => !v)}
                  style={paginationBtn}
                  title="Show/hide columns"
                >
                  Columns ▾
                </button>
                {colPickerOpen && (
                  <div
                    style={{
                      position: 'absolute',
                      bottom: '100%',
                      right: 0,
                      marginBottom: 4,
                      background: '#fff',
                      border: '1px solid #d0d7e0',
                      borderRadius: 4,
                      boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
                      padding: '0.4rem 0.6rem',
                      fontSize: '0.78rem',
                      zIndex: 10,
                      minWidth: 160,
                    }}
                  >
                    {table.getAllLeafColumns().map((col) => (
                      <label
                        key={col.id}
                        style={{
                          display: 'flex',
                          gap: '0.4rem',
                          padding: '0.15rem 0',
                          cursor: 'pointer',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={col.getIsVisible()}
                          onChange={col.getToggleVisibilityHandler()}
                        />
                        {String(col.columnDef.header ?? col.id)}
                      </label>
                    ))}
                  </div>
                )}
              </div>
              {(meta.data?.sql_explanation || meta.data?.sql) && (
                <button
                  type="button"
                  onClick={() => setSqlModalOpen(true)}
                  style={paginationBtn}
                  title="See what this search matches and the underlying SQL"
                >
                  Explain Search
                </button>
              )}
              <a
                href={`/api/searches/${encodeURIComponent(searchId)}/csv`}
                style={{
                  ...paginationBtn,
                  color: '#222',
                  textDecoration: 'none',
                  background: '#fff',
                }}
              >
                Download CSV
              </a>
              <button type="button" onClick={() => setXnatModalOpen(true)} style={paginationBtn}>
                Send to XNAT
              </button>
              <button
                type="button"
                onClick={() => {
                  const next = !iframeExpanded;
                  setIframeExpanded(next);
                  setIframeHeight(next ? HEIGHT_EXPANDED : HEIGHT_COMPACT);
                }}
                title={
                  iframeExpanded
                    ? 'Shrink viewer back to compact size'
                    : 'Grow viewer for more room'
                }
                aria-label={iframeExpanded ? 'Contract viewer' : 'Expand viewer'}
                style={{
                  ...paginationBtn,
                  display: 'inline-flex',
                  alignItems: 'center',
                  padding: '0.2rem 0.35rem',
                }}
              >
                {iframeExpanded ? <ContractIcon /> : <ExpandIcon />}
              </button>
            </div>
          </div>
        )
      )}
      {xnatModalOpen && (
        <SendToXnatModal
          searchId={searchId}
          total={total}
          onClose={() => setXnatModalOpen(false)}
        />
      )}
      {sqlModalOpen && (
        <ExplainSqlModal
          explanation={meta.data?.sql_explanation ?? ''}
          sql={meta.data?.sql ?? ''}
          highlightTerms={meta.data?.match_terms ?? []}
          highlightDiagnosis={meta.data?.match_diagnoses ?? []}
          onClose={() => setSqlModalOpen(false)}
        />
      )}
      {filtersModalOpen && (
        <FiltersModal
          initial={appliedFilters}
          onApply={(next) => {
            setAppliedFilters(next);
            setPage(1);
            setFiltersModalOpen(false);
          }}
          onRefineInChat={(next) => {
            setAppliedFilters(next);
            setPage(1);
            applyFilterToChat(searchId, next);
            setFiltersModalOpen(false);
          }}
          onClose={() => setFiltersModalOpen(false)}
        />
      )}
    </div>
  );
}

// `input:prompt` (not `:submit`) on purpose: OWUI's auto-submit gates
// on real same-origin between iframe and chat, not on the
// `iframeSandboxAllowSameOrigin` user setting. Scout's iframe is on a
// dedicated subdomain, so :submit would force a confirmation dialog
// every click. Filling the composer and letting the user hit Enter
// avoids the dialog. See OWUI docs / Chat.svelte handler.
function submitChatPrompt(text: string): void {
  if (window.parent === window) return;
  window.parent.postMessage({ type: 'input:prompt', text }, chatOrigin());
}

function applyFilterToChat(searchId: string, filters: FilterState): void {
  const clauses: string[] = [];
  if (filters.patient_age) {
    const { min, max } = filters.patient_age;
    if (min && max) clauses.push(`patient_age between ${min} and ${max}`);
    else if (min) clauses.push(`patient_age >= ${min}`);
    else if (max) clauses.push(`patient_age <= ${max}`);
  }
  if (filters.message_dt) {
    const { min, max } = filters.message_dt;
    if (min && max) clauses.push(`message_dt between ${min} and ${max}`);
    else if (min) clauses.push(`message_dt >= ${min}`);
    else if (max) clauses.push(`message_dt <= ${max}`);
  }
  if (filters.sex && filters.sex.length > 0) {
    clauses.push(`sex in (${filters.sex.join(', ')})`);
  }
  if (filters.modality && filters.modality.length > 0) {
    clauses.push(`modality in (${filters.modality.join(', ')})`);
  }
  if (filters.service_name) {
    clauses.push(`service_name contains "${filters.service_name}"`);
  }
  if (clauses.length === 0) return;
  submitChatPrompt(`Refine search ${searchId}. Filter rows where ${clauses.join(', ')}.`);
}

function discussInChat(sourceFile: string): void {
  submitChatPrompt(
    `Read the report at \`${sourceFile}\`. Walk me through the findings, impression, and key diagnoses.`,
  );
}

// Send-to-XNAT modal. The Send button is a no-op TBD - backend XNAT
// push is not wired yet.
function SendToXnatModal(props: { searchId: string; total: number; onClose: () => void }) {
  const [project, setProject] = useState('');
  const [irb, setIrb] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const canSubmit = !!project && !!irb && confirmed;
  const onSend = () => {
    alert(
      `TBD - XNAT push not wired yet.\n\nWould send ${props.total} accessions from ${props.searchId} to project "${project}" under IRB ${irb}.`,
    );
    props.onClose();
  };
  return (
    <Modal onClose={props.onClose} minWidth={380} maxWidth={520}>
      <div style={{ fontSize: '0.9rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '1rem' }}>Send to XNAT</h3>
        <p style={{ color: '#555', margin: '0 0 1rem', fontSize: '0.85rem' }}>
          This will push <strong>{props.total.toLocaleString()}</strong> accessions from{' '}
          <code>{props.searchId}</code> to an XNAT project. Walk through each step before sending.
        </p>

        <label style={{ display: 'block', marginBottom: '0.4rem', fontWeight: 600 }}>
          1. XNAT project
        </label>
        <select
          value={project}
          onChange={(e) => setProject(e.target.value)}
          style={{ width: '100%', padding: '0.4rem', marginBottom: '0.75rem' }}
        >
          <option value="">- select project -</option>
          <option value="SCOUT_DEMO">SCOUT_DEMO</option>
          <option value="RADIOLOGY_RES">RADIOLOGY_RES</option>
        </select>

        <label style={{ display: 'block', marginBottom: '0.4rem', fontWeight: 600 }}>
          2. IRB / protocol number
        </label>
        <input
          type="text"
          value={irb}
          onChange={(e) => setIrb(e.target.value)}
          placeholder="e.g. IRB-2024-1234"
          style={{
            width: '100%',
            padding: '0.4rem',
            marginBottom: '0.75rem',
            boxSizing: 'border-box',
          }}
        />

        <label style={{ display: 'block', marginBottom: '0.75rem' }}>
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />{' '}
          3. I confirm these reports are covered by the IRB above and that I am authorized to export
          them to XNAT.
        </label>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: '0.5rem',
            marginTop: '1rem',
          }}
        >
          <button type="button" onClick={props.onClose} style={paginationBtn}>
            Cancel
          </button>
          <button
            type="button"
            onClick={onSend}
            disabled={!canSubmit}
            style={{
              ...paginationBtn,
              background: canSubmit ? '#4477AA' : '#bbb',
              color: '#fff',
              borderColor: canSubmit ? '#4477AA' : '#bbb',
              cursor: canSubmit ? 'pointer' : 'not-allowed',
            }}
          >
            Send
          </button>
        </div>
      </div>
    </Modal>
  );
}

const paginationBtn: React.CSSProperties = {
  fontSize: '0.72rem',
  padding: '0.2rem 0.45rem',
  border: '1px solid #aaa',
  background: '#fff',
  borderRadius: 3,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

function RowDetail(props: {
  row: Record<string, unknown>;
  idColumn: string;
  highlightTerms: string[];
  highlightDiagnosis: string[];
}) {
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
  const highlightRe = escaped.length ? new RegExp(`\\b(${escaped.join('|')})\\b`, 'gi') : null;

  // Strip SQL-LIKE `%` so the LLM can pass `R91` or `R91%` - same thing.
  const dxPrefixes = props.highlightDiagnosis
    .map((d) => d.trim().replace(/%+$/, '').toLowerCase())
    .filter((d) => d.length >= 1);

  const applyTextHighlights = (text: string): React.ReactNode => {
    if (!text) return null;
    if (!highlightRe) return text;
    const parts = text.split(highlightRe);
    return parts.map((p, i) =>
      i % 2 === 1 ? (
        <mark key={i} style={{ background: '#fff3a3', padding: '0 1px' }}>
          {p}
        </mark>
      ) : (
        <React.Fragment key={i}>{p}</React.Fragment>
      ),
    );
  };

  // Prefer the lazy /reports fetch (all fields) but fall back to the slim
  // /rows row so the panel doesn't pop in late.
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
    <div style={{ fontSize: '0.78rem', lineHeight: 1.4, color: '#222' }}>
      <div
        style={{
          padding: '0.5rem 0.7rem',
          marginBottom: '0.5rem',
          background: '#fff',
          border: '1px solid #d8dde3',
          borderRadius: 4,
          fontSize: '0.74rem',
          boxShadow: '0 1px 4px rgba(31, 95, 168, 0.10)',
        }}
      >
        <CardRow>
          <CardField label="MRN" value={fmt(meta.epic_mrn)} mono />
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
              color: '#5a6a7f',
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
                    background: positive ? '#fff3a3' : '#f4f4f6',
                    border: positive ? '1px solid #d6b500' : '1px solid #e2e2e2',
                    fontSize: '0.72rem',
                    color: '#222',
                    fontWeight: positive ? 600 : 400,
                  }}
                >
                  <code
                    style={{
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      color: positive ? '#7a5a00' : '#4477AA',
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

      {reportQ.isLoading && <div style={{ color: '#888' }}>Loading report…</div>}
      {reportQ.error && (
        <div style={{ color: '#b00' }}>{friendlyError(reportQ.error, 'this report')}</div>
      )}
      {reportQ.data && (
        <div
          style={{
            whiteSpace: 'pre-wrap',
            color: '#333',
            background: '#fff',
            border: '1px solid #e2e2e2',
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
          ) || <em style={{ color: '#888' }}>(empty)</em>}
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
                color: '#888',
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
            <button type="button" onClick={() => discussInChat(sourceFile)} style={paginationBtn}>
              Discuss in Chat
            </button>
          </div>
        );
      })()}
    </div>
  );
}

// Octicons copy / check / close (16px viewBox, MIT). Inline to avoid
// pulling an icon dependency for three buttons.
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

function ExplainSqlModal(props: {
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

function CardRow(props: { children: React.ReactNode }) {
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

const SEX_OPTIONS = ['M', 'F', 'U'] as const;
const MODALITY_OPTIONS = [
  '3D',
  'CT',
  'CTA',
  'DXA',
  'ECH',
  'FL',
  'IR',
  'MG',
  'MR',
  'MRA',
  'NM',
  'PET',
  'US',
  'XR',
] as const;

function FiltersModal(props: {
  initial: FilterState;
  onApply: (next: FilterState) => void;
  onRefineInChat: (next: FilterState) => void;
  onClose: () => void;
}) {
  const [staged, setStaged] = useState<FilterState>(props.initial);

  const setAgeBound = (which: 'min' | 'max', value: string) =>
    setStaged((s) => ({
      ...s,
      patient_age: { ...s.patient_age, [which]: value || undefined },
    }));
  const setDateBound = (which: 'min' | 'max', value: string) =>
    setStaged((s) => ({
      ...s,
      message_dt: { ...s.message_dt, [which]: value || undefined },
    }));
  const toggleEnum = (col: 'sex' | 'modality', value: string) =>
    setStaged((s) => {
      const cur = new Set(s[col] ?? []);
      if (cur.has(value)) cur.delete(value);
      else cur.add(value);
      const next = Array.from(cur);
      return { ...s, [col]: next.length > 0 ? next : undefined };
    });
  const setServiceName = (value: string) =>
    setStaged((s) => ({ ...s, service_name: value || undefined }));

  return (
    <Modal
      onClose={props.onClose}
      ariaLabel="Filter rows"
      minWidth={420}
      maxWidth={560}
      maxHeight="calc(100vh - 40px)"
    >
      <div style={{ fontSize: '0.85rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '1rem' }}>Filter rows</h3>

        <FieldRow label="Age">
          <RangeInputs
            min={staged.patient_age?.min ?? ''}
            max={staged.patient_age?.max ?? ''}
            inputType="number"
            placeholder={{ min: 'min', max: 'max' }}
            onChange={setAgeBound}
          />
        </FieldRow>

        <FieldRow label="Sex">
          <CheckboxRow
            options={SEX_OPTIONS as readonly string[]}
            selected={staged.sex ?? []}
            onToggle={(v) => toggleEnum('sex', v)}
          />
        </FieldRow>

        <FieldRow label="Modality">
          <CheckboxRow
            options={MODALITY_OPTIONS as readonly string[]}
            selected={staged.modality ?? []}
            onToggle={(v) => toggleEnum('modality', v)}
          />
        </FieldRow>

        <FieldRow label="Date">
          <RangeInputs
            min={staged.message_dt?.min ?? ''}
            max={staged.message_dt?.max ?? ''}
            inputType="date"
            placeholder={{ min: 'from', max: 'to' }}
            onChange={setDateBound}
          />
        </FieldRow>

        <FieldRow label="Service">
          <input
            type="text"
            value={staged.service_name ?? ''}
            onChange={(e) => setServiceName(e.target.value)}
            placeholder="contains…"
            style={{
              width: '100%',
              fontSize: '0.85rem',
              padding: '0.3rem 0.45rem',
              border: '1px solid #ccc',
              borderRadius: 3,
              boxSizing: 'border-box',
            }}
          />
        </FieldRow>

        <div
          style={{
            marginTop: '1rem',
            paddingTop: '0.75rem',
            borderTop: '1px solid #eee',
            display: 'flex',
            gap: '0.5rem',
            alignItems: 'center',
          }}
        >
          <button type="button" onClick={() => setStaged({})} style={paginationBtn}>
            Reset
          </button>
          <span style={{ flex: 1 }} />
          <button type="button" onClick={props.onClose} style={paginationBtn}>
            Cancel
          </button>
          <button type="button" onClick={() => props.onRefineInChat(staged)} style={paginationBtn}>
            Filter via Chat
          </button>
          <button
            type="button"
            onClick={() => props.onApply(staged)}
            style={{
              ...paginationBtn,
              background: '#4477AA',
              color: '#fff',
              borderColor: '#4477AA',
            }}
          >
            Apply
          </button>
        </div>
      </div>
    </Modal>
  );
}

function FieldRow(props: { label: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.75rem',
        padding: '0.4rem 0',
      }}
    >
      <div style={{ width: 80, color: '#555', fontWeight: 600, paddingTop: '0.25rem' }}>
        {props.label}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>{props.children}</div>
    </div>
  );
}

function RangeInputs(props: {
  min: string;
  max: string;
  inputType: 'number' | 'date';
  placeholder: { min: string; max: string };
  onChange: (which: 'min' | 'max', value: string) => void;
}) {
  const style: React.CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: '0.85rem',
    padding: '0.3rem 0.45rem',
    border: '1px solid #ccc',
    borderRadius: 3,
    boxSizing: 'border-box',
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
      <input
        type={props.inputType}
        value={props.min}
        onChange={(e) => props.onChange('min', e.target.value)}
        placeholder={props.placeholder.min}
        style={style}
      />
      <span style={{ color: '#888' }}>-</span>
      <input
        type={props.inputType}
        value={props.max}
        onChange={(e) => props.onChange('max', e.target.value)}
        placeholder={props.placeholder.max}
        style={style}
      />
    </div>
  );
}

function CheckboxRow(props: {
  options: readonly string[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  const set = new Set(props.selected);
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem 0.75rem' }}>
      {props.options.map((opt) => (
        <label
          key={opt}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.25rem',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          <input type="checkbox" checked={set.has(opt)} onChange={() => props.onToggle(opt)} />
          {opt}
        </label>
      ))}
    </div>
  );
}

function ExpandIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6V3h3M10 3h3v3M13 10v3h-3M6 13H3v-3" />
    </svg>
  );
}

function ContractIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 3v3H3M13 6h-3V3M10 13v-3h3M3 10h3v3" />
    </svg>
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
      <span style={{ color: '#888', fontWeight: 600 }}>{props.label}</span>
      <span
        style={{
          color: '#222',
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
