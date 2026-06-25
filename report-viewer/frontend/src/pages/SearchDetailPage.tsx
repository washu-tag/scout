import React, { useEffect, useMemo, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { isEmbedded } from '../embed';
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
import { getSearch, getSearchRows, getReport } from '../api/client';
import { HEIGHT_COMPACT, HEIGHT_EXPANDED, setHeight as setIframeHeight } from '../iframeHeight';

const ROW_ACTIVE_BG = '#e8f0fa';
const DETAIL_ZONE_BG = '#f0f6fc';

// Locked column set, mirrors the legacy iframe viewer. The /rows
// endpoint returns the full SELECTed column set; we render only these
// preferred ones to keep the table readable when the LLM SELECTs *
// from the wide reports_latest_epic_view.
//
// `embedWidth` is the min-width used inside the OWUI chat iframe
// (~900px wide); `width` is the standalone-page min-width. The
// embed sizes are tuned to fit without horizontal scroll in the chat
// embed. `embedHidden` drops a column entirely when embedded.
const COLUMNS_CONFIG: Array<{
  field: string;
  title: string;
  width?: number;
  embedWidth?: number;
  embedHidden?: boolean;
  align?: 'right' | 'center';
  mono?: boolean;
  kind?: 'date';
}> = [
  { field: 'accession_number', title: 'Acc', width: 110, embedWidth: 85, mono: true },
  { field: 'epic_mrn', title: 'MRN', width: 105, embedWidth: 80, mono: true },
  { field: 'message_dt', title: 'Date', width: 125, embedWidth: 100, kind: 'date' },
  { field: 'modality', title: 'Modality', width: 80, embedWidth: 60 },
  { field: 'service_name', title: 'Service', width: 240, embedWidth: 180 },
  // Facility is the least-clicked column; drop in embed mode to free space.
  { field: 'sending_facility', title: 'Facility', width: 120, embedHidden: true },
  { field: 'patient_age', title: 'Age', width: 110, embedWidth: 90, align: 'right' },
  { field: 'sex', title: 'Sex', width: 50, embedWidth: 40, align: 'center' },
  { field: 'evidence', title: 'Label', width: 110, embedHidden: true },
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
  if (!v) return '—';
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
  // Two-tier filter state:
  //  - filterInputs: drives the <input> values, updates immediately on
  //    each keystroke (no lag, focus is preserved).
  //  - filters: debounced copy used in the queryKey + sent to the backend.
  // Without this split, every keystroke fires a query refetch, and the
  // "no data yet" branch unmounts the table mid-type which yanks focus.
  const [filterInputs, setFilterInputs] = useState<Record<string, string>>({});
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [xnatModalOpen, setXnatModalOpen] = useState(false);
  const [sqlModalOpen, setSqlModalOpen] = useState(false);
  const [colPickerOpen, setColPickerOpen] = useState(false);
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({});
  const [iframeExpanded, setIframeExpanded] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => {
      setFilters(filterInputs);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [filterInputs]);

  const meta = useQuery({
    queryKey: ['search', searchId],
    queryFn: () => getSearch(searchId),
    enabled: !!searchId,
  });

  // Sort/filter state goes into the cache key so a change re-fetches.
  // Sort string normalized via JSON so the key is stable.
  const sortParam = sorting[0]
    ? { col: sorting[0].id, dir: (sorting[0].desc ? 'desc' : 'asc') as 'asc' | 'desc' }
    : null;
  const rowsQ = useQuery({
    queryKey: ['search', searchId, 'rows', page, limit, sortParam, filters],
    queryFn: () => getSearchRows(searchId, { page, limit, sort: sortParam, filters }),
    enabled: !!searchId,
    // Keep the previously-fetched page visible while a sort/filter/page
    // change is in flight. Without this, the table unmounts on every
    // refetch, the "Loading rows…" branch takes over, and a debounced
    // filter input would still lose focus because the whole subtree
    // re-mounts when data lands.
    placeholderData: keepPreviousData,
  });

  // Row-expansion state is keyed by row index; clear it when the row
  // set actually changes so a page-2 row at index 0 doesn't inherit
  // page-1's expanded card. Gating on rowsQ.data (not page) avoids a
  // mid-fetch collapse flash thanks to keepPreviousData.
  useEffect(() => {
    setExpanded({});
  }, [rowsQ.data]);

  // total comes from the rows response when filters are active (it's
  // the post-filter count). Fall back to search meta when there's no
  // filter applied.
  const total = rowsQ.data?.total ?? meta.data?.count ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / limit));

  // Build columns once per row-shape change. Lock to COLUMNS_CONFIG —
  // anything else in the row stays in the underlying data (and in the
  // CSV download) but isn't surfaced as a column.
  const available = useMemo<string[]>(
    () => rowsQ.data?.columns ?? (rowsQ.data?.rows?.[0] ? Object.keys(rowsQ.data.rows[0]) : []),
    [rowsQ.data],
  );

  const embeddedNow = isEmbedded();
  const columns = useMemo(
    () =>
      COLUMNS_CONFIG.filter(
        (c) => available.includes(c.field) && !(embeddedNow && c.embedHidden),
      ).map((c) => {
        const initialWidth = embeddedNow ? (c.embedWidth ?? c.width) : c.width;
        return columnHelper.accessor((row: Row) => row[c.field], {
          id: c.field,
          header: c.title,
          size: initialWidth,
          cell: (info) => (c.kind === 'date' ? fmtDate(info.getValue()) : fmtCell(info.getValue())),
          meta: { align: c.align, mono: c.mono },
        });
      }),
    [available, embeddedNow],
  );

  const data = rowsQ.data?.rows ?? [];

  const table = useReactTable({
    data,
    columns,
    state: { sorting, expanded, columnVisibility },
    onSortingChange: (updater) => {
      // Server-side sort: state change drives a refetch via queryKey.
      // Reset to page 1 so users don't end up on an out-of-range page.
      setSorting(updater);
      setPage(1);
    },
    onExpandedChange: setExpanded,
    onColumnVisibilityChange: setColumnVisibility,
    getRowCanExpand: () => true,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    // Manual sort — server returns rows already ordered. Disable the
    // client-side sort model so it doesn't re-sort what the server
    // already sorted (and only across the visible page).
    manualSorting: true,
    columnResizeMode: 'onChange',
    defaultColumn: { minSize: 40 },
  });

  const embedded = embeddedNow;
  return (
    <div>
      {!embedded && (
        <div style={{ marginBottom: '0.75rem' }}>
          <Link to="/" style={{ fontSize: '0.85rem', color: '#4477AA' }}>
            ← All searches
          </Link>
        </div>
      )}
      {rowsQ.error && (
        <p style={{ color: '#b00' }}>Failed to load rows: {(rowsQ.error as Error).message}</p>
      )}
      {/* Always render the table once we have anything (including the
          previous data via keepPreviousData). The wrapping ternary
          shows a single first-load skeleton when there's no data at
          all yet — otherwise the existing data stays put and the
          header shows the "Updating" pill. */}
      {!rowsQ.data && rowsQ.isLoading ? (
        <p style={{ color: '#666' }}>Loading rows…</p>
      ) : (
        rowsQ.data && (
          <>
            <div
              style={{
                overflowX: 'auto',
                overflowY: 'auto',
                // -80px reserves room for the bottom pagination row.
                maxHeight: embedded
                  ? iframeExpanded
                    ? HEIGHT_EXPANDED - 80
                    : HEIGHT_COMPACT - 80
                  : 'calc(100vh - 220px)',
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
                  // Fixed layout makes the <th> widths authoritative
                  // so the resize state is what actually renders.
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
                              padding: embedded ? '0.35rem 0.45rem' : '0.5rem 0.75rem',
                              fontSize: embedded ? '0.78rem' : 'inherit',
                              fontWeight: 600,
                              color: '#555',
                              background: '#f5f5f5',
                              borderBottom: '1px solid #e2e2e2',
                              whiteSpace: 'nowrap',
                              width: header.getSize(),
                              cursor: 'pointer',
                              userSelect: 'none',
                              // Sticky header so column titles stay visible
                              // while scrolling rows in the bounded table.
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
                  {/* Filter row — one text input per column. Substring match,
                    case-insensitive on the server. Pinned just below the
                    sticky title row so rows underneath don't leak through
                    a gap while scrolling. The patient_age column gets a
                    two-input min–max range instead; range keys are stored
                    as `patient_age.min` / `patient_age.max` and shipped to
                    the backend as the matching dotted query params. */}
                  <tr>
                    {table.getHeaderGroups()[0]?.headers.map((header) => (
                      <th
                        key={`f-${header.id}`}
                        style={{
                          padding: '0.25rem 0.4rem',
                          background: '#fff',
                          borderBottom: '1px solid #e2e2e2',
                          position: 'sticky',
                          // Sticky-stack right under the title row. Heights:
                          // embedded title row ≈ 28px (smaller font),
                          // standalone title row ≈ 36px. Use a fixed
                          // value tuned to whichever mode we're in.
                          top: embedded ? 28 : 36,
                          zIndex: 2,
                        }}
                      >
                        {header.id === 'patient_age' ? (
                          <AgeRangeFilter
                            min={filterInputs['patient_age.min'] ?? ''}
                            max={filterInputs['patient_age.max'] ?? ''}
                            onChange={(which, value) => {
                              const next = { ...filterInputs };
                              const key = `patient_age.${which}`;
                              if (value) next[key] = value;
                              else delete next[key];
                              setFilterInputs(next);
                            }}
                          />
                        ) : (
                          <input
                            type="text"
                            value={filterInputs[header.id] ?? ''}
                            onChange={(e) => {
                              const next = { ...filterInputs };
                              if (e.target.value) next[header.id] = e.target.value;
                              else delete next[header.id];
                              setFilterInputs(next);
                            }}
                            placeholder="Filter…"
                            style={{
                              width: '100%',
                              fontSize: '0.78rem',
                              padding: '0.15rem 0.35rem',
                              border: '1px solid #ccc',
                              borderRadius: 2,
                              boxSizing: 'border-box',
                            }}
                          />
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => {
                    const isExpanded = row.getIsExpanded();
                    return (
                      <React.Fragment key={row.id}>
                        <tr
                          onClick={(e) => {
                            row.toggleExpanded();
                            // Scroll the clicked row into view so the
                            // expanded panel beneath isn't pushed below
                            // the iframe's visible region. Run after the
                            // detail row mounts (microtask).
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
                                  padding: embedded ? '0.3rem 0.45rem' : '0.4rem 0.75rem',
                                  fontSize: embedded ? '0.78rem' : 'inherit',
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
                                    ...(meta.data?.highlight_terms ?? []),
                                    // Drop numeric range bounds (patient_age.min/.max)
                                    // — they're not searchable text.
                                    ...Object.entries(filters)
                                      .filter(([k, v]) => v && !k.includes('.'))
                                      .map(([, v]) => v),
                                  ]}
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
              }}
            >
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                style={embedded ? paginationBtnEmbed : paginationBtn}
              >
                Prev
              </button>
              <span>
                Page {page} of {lastPage}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
                disabled={page >= lastPage}
                style={embedded ? paginationBtnEmbed : paginationBtn}
              >
                Next
              </button>
              <span style={{ marginLeft: '1rem', color: '#888' }}>Rows per page:</span>
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
              <span style={{ color: '#666', fontSize: embedded ? '0.75rem' : '0.8rem' }}>
                {meta.isLoading
                  ? 'Loading…'
                  : meta.error
                    ? 'Failed to load metadata'
                    : `${total.toLocaleString()} rows`}
              </span>
              {/* Reserved slot; visibility toggles instead of mount so
                  the row doesn't reflow when a fetch starts. */}
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
              {Object.keys(filters).length > 0 && (
                <button
                  type="button"
                  onClick={() => applyFilterToChat(filters)}
                  style={{
                    ...(embedded ? paginationBtnEmbed : paginationBtn),
                    background: '#4477AA',
                    color: '#fff',
                    borderColor: '#4477AA',
                  }}
                >
                  {embedded ? 'Apply to chat' : 'Apply filter to chat'}
                </button>
              )}
              {/* Column visibility picker — quick dropdown above the
                button rather than a modal. Lets users toggle which
                visible-table columns are shown. */}
              <div style={{ position: 'relative' }}>
                <button
                  type="button"
                  onClick={() => setColPickerOpen((v) => !v)}
                  style={embedded ? paginationBtnEmbed : paginationBtn}
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
              {(meta.data?.sql_explanation || meta.data?.source_sql) && (
                <button
                  type="button"
                  onClick={() => setSqlModalOpen(true)}
                  style={embedded ? paginationBtnEmbed : paginationBtn}
                  title="See what this search matches and the underlying SQL"
                >
                  Explain Search
                </button>
              )}
              <a
                href={`/api/searches/${encodeURIComponent(searchId)}/csv`}
                style={{
                  ...(embedded ? paginationBtnEmbed : paginationBtn),
                  color: '#222',
                  textDecoration: 'none',
                  background: '#fff',
                }}
              >
                Download CSV
              </a>
              <button
                type="button"
                onClick={() => setXnatModalOpen(true)}
                style={embedded ? paginationBtnEmbed : paginationBtn}
              >
                Send to XNAT
              </button>
              {embedded && (
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
                    ...paginationBtnEmbed,
                    display: 'inline-flex',
                    alignItems: 'center',
                    padding: '0.2rem 0.35rem',
                  }}
                >
                  {iframeExpanded ? <ContractIcon /> : <ExpandIcon />}
                </button>
              )}
            </div>
          </>
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
          sql={meta.data?.source_sql ?? ''}
          onClose={() => setSqlModalOpen(false)}
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
  window.parent.postMessage({ type: 'input:prompt', text }, '*');
}

function applyFilterToChat(filters: Record<string, string>) {
  const clauses: string[] = [];
  const ageMin = filters['patient_age.min'];
  const ageMax = filters['patient_age.max'];
  if (ageMin && ageMax) clauses.push(`patient_age between ${ageMin} and ${ageMax}`);
  else if (ageMin) clauses.push(`patient_age >= ${ageMin}`);
  else if (ageMax) clauses.push(`patient_age <= ${ageMax}`);
  for (const [col, val] of Object.entries(filters)) {
    if (!val || col.startsWith('patient_age.')) continue;
    clauses.push(`${col} contains "${val}"`);
  }
  submitChatPrompt(`Filter rows where ${clauses.join(' and ')}.`);
}

function discussInChat(sourceFile: string): void {
  submitChatPrompt(
    `Read the report at \`${sourceFile}\`. Walk me through the findings, impression, and key diagnoses.`,
  );
}

// Send-to-XNAT modal. V1 placeholder — walks the user through the
// steps (project pick, IRB attestation, accession review) but the
// Send button is a no-op TBD. Backend XNAT push lands later.
function SendToXnatModal(props: { searchId: string; total: number; onClose: () => void }) {
  const [project, setProject] = useState('');
  const [irb, setIrb] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const canSubmit = !!project && !!irb && confirmed;
  const onSend = () => {
    alert(
      `TBD — XNAT push not wired yet.\n\nWould send ${props.total} accessions from ${props.searchId} to project "${project}" under IRB ${irb}.`,
    );
    props.onClose();
  };
  return (
    <div
      role="dialog"
      aria-modal="true"
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
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          padding: '1.25rem 1.5rem',
          borderRadius: 6,
          minWidth: 380,
          maxWidth: 520,
          boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
          fontSize: '0.9rem',
        }}
      >
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
          <option value="">— select project —</option>
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
    </div>
  );
}

const paginationBtn: React.CSSProperties = {
  fontSize: '0.85rem',
  padding: '0.25rem 0.7rem',
  border: '1px solid #aaa',
  background: '#fff',
  borderRadius: 3,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

// Tighter button styling for the chat-iframe context — the embed is
// ~900px wide and the bottom row has 7 buttons when Apply-filter is
// active. Smaller padding + font + nowrap keeps each one compact.
const paginationBtnEmbed: React.CSSProperties = {
  fontSize: '0.72rem',
  padding: '0.2rem 0.45rem',
  border: '1px solid #aaa',
  background: '#fff',
  borderRadius: 3,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

// Detail panel rendered when a row is expanded. Lazy-fetches the full
// report on first expand (kept in TanStack Query cache so reopening is
// instant). Default view: the full report_text — uniform across older
// reports (no parsed sections) and newer ones. Diagnoses chip-row
// underneath. Highlight terms come from the active column filters; the
// matches light up via client-side regex (no server-side snippet
// extraction, no stored snippet column).
function RowDetail(props: {
  row: Record<string, unknown>;
  idColumn: string;
  highlightTerms: string[];
}) {
  // The row's identifier for the /reports lookup. The search's
  // id_column value lives on the slim /rows row under the same key.
  const reportId = String(props.row[props.idColumn] ?? '');
  const reportQ = useQuery({
    queryKey: ['report', props.idColumn, reportId],
    queryFn: () => getReport(reportId, props.idColumn),
    enabled: !!reportId,
    staleTime: 5 * 60_000, // 5 min — same row reopens instantly
  });

  // Escape regex special chars in user-supplied filter values, then
  // join into one alternation pattern. Case-insensitive.
  const escaped = props.highlightTerms
    .map((t) => t.trim())
    .filter((t) => t.length >= 2)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const highlightRe = escaped.length ? new RegExp(`(${escaped.join('|')})`, 'gi') : null;

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

  // Diagnoses live on the full /reports/{id} fetch — the slim /rows
  // payload only carries the LLM's SELECT columns, which usually omits
  // `diagnoses`. Prefer reportQ when it's landed; fall back to whatever
  // the row already has so the panel doesn't sit empty for the LLMs that
  // do include `diagnoses` in their SELECT.
  const diagnoses = reportQ.data?.diagnoses ?? props.row.diagnoses;

  // De-dup the highlight terms (LLM-time + filter-time can overlap)
  // and drop short / empty entries that would match too much.
  const uniqueTerms = Array.from(
    new Set(props.highlightTerms.map((t) => t.trim()).filter((t) => t.length >= 2)),
  );

  // Metadata sources. Prefer the lazy-fetched ReportDetail when it's
  // landed (more fields, fresher), but fall back to whatever the slim
  // /rows row already has so the card doesn't pop in late.
  const meta = reportQ.data ?? props.row;
  const fmt = (v: unknown): string => {
    if (v === null || v === undefined) return '—';
    const s = String(v);
    return s.length > 0 ? s : '—';
  };
  const fmtPerson = (v: unknown): string => {
    if (!v) return '—';
    if (Array.isArray(v)) return v.length ? v.join(', ') : '—';
    return String(v);
  };

  // Identify which diagnosis codes / texts match the search's
  // highlight_terms so we can call them out visually — those are the
  // "the LLM was right" signal at a glance.
  const dxList = Array.isArray(diagnoses) ? (diagnoses as Array<Record<string, unknown>>) : [];
  const positiveDxIndex = new Set<number>();
  if (highlightRe) {
    for (let i = 0; i < dxList.length; i++) {
      const code = String(dxList[i].diagnosis_code ?? '');
      const text = String(dxList[i].diagnosis_code_text ?? '');
      // Reset regex state — lastIndex sticks across .test() calls.
      highlightRe.lastIndex = 0;
      if (highlightRe.test(code + ' ' + text)) positiveDxIndex.add(i);
    }
  }

  // ReportDetail extras land asynchronously; cast for the fields the
  // slim /rows row doesn't carry. fmt() returns "—" for null/undefined
  // so missing fields don't break the card layout.
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
      {/* Report card — three compact sections sized to fit inside the
          500px iframe without scrolling away from anything important.
          Each row is a sequence of label·value pairs separated by
          subtle bullets so a clinician can scan left-to-right. The
          shadow lifts the card off the blue-tinted detail zone so it
          reads as a distinct surface, not "another row." */}
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
          <CardField label="Acc" value={fmt(meta.accession_number)} mono />
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

      {/* Diagnoses chips — TOP of panel so the user sees the ICD
          evidence first. Positive matches (codes/text matching the
          search's highlight terms) get a yellow background + bold so
          the eye lands on them. Non-matching codes are still visible
          but greyed. */}
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

      {/* Compact "highlighting" hint above the report text. */}
      {uniqueTerms.length > 0 && (
        <div style={{ color: '#666', fontSize: '0.7rem', marginBottom: '0.25rem' }}>
          Highlighting:{' '}
          {uniqueTerms.map((t, i) => (
            <code
              key={i}
              style={{
                background: '#fff3a3',
                padding: '0 4px',
                marginRight: 4,
                borderRadius: 2,
              }}
            >
              {t}
            </code>
          ))}
        </div>
      )}

      {reportQ.isLoading && <div style={{ color: '#888' }}>Loading report…</div>}
      {reportQ.error && (
        <div style={{ color: '#b00' }}>
          Failed to load report: {(reportQ.error as Error).message}
        </div>
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
          <div style={{ marginTop: '0.5rem', display: 'flex', justifyContent: 'flex-end' }}>
            <button type="button" onClick={() => discussInChat(sourceFile)} style={paginationBtn}>
              Discuss in Chat
            </button>
          </div>
        );
      })()}
    </div>
  );
}

// "Explain SQL" modal — LLM-written explanation + raw SQL. Opened
// from the Explain SQL button in the bottom action row so we don't
// burn real estate above the table.
function ExplainSqlModal(props: { explanation: string; sql: string; onClose: () => void }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
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
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          padding: '1.25rem 1.5rem',
          borderRadius: 6,
          minWidth: 480,
          maxWidth: 760,
          maxHeight: '80vh',
          overflowY: 'auto',
          boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
          fontSize: '0.9rem',
        }}
      >
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '1rem' }}>What this search matches</h3>
        {props.explanation ? (
          <p style={{ margin: '0 0 1rem', lineHeight: 1.5 }}>{props.explanation}</p>
        ) : (
          <p style={{ margin: '0 0 1rem', color: '#888', fontStyle: 'italic' }}>
            No plain-language explanation was attached to this search (older searches, or the model
            didn&apos;t supply one).
          </p>
        )}
        <div style={{ fontWeight: 600, marginBottom: '0.35rem', fontSize: '0.85rem' }}>SQL</div>
        <pre
          style={{
            background: '#f4f7fb',
            border: '1px solid #d0dceb',
            borderRadius: 3,
            padding: '0.6rem 0.75rem',
            fontSize: '0.74rem',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            margin: 0,
          }}
        >
          {props.sql || '(no SQL recorded)'}
        </pre>
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            marginTop: '1rem',
          }}
        >
          <button type="button" onClick={props.onClose} style={paginationBtn}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// CardRow / CardField — inline label·value pairs separated by a faint
// bullet. Wraps naturally so on narrow widths fields stack onto the
// next line. Replaces the older grid-based MetaCell which forced its
// own row per pair.
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

// Two narrow number inputs separated by a "—", driving the
// patient_age min/max range filter. Either side can be left blank
// for an open-ended range; the backend reads them as filter.patient_age.min
// and filter.patient_age.max.
function AgeRangeFilter(props: {
  min: string;
  max: string;
  onChange: (which: 'min' | 'max', value: string) => void;
}) {
  const inputStyle: React.CSSProperties = {
    width: '100%',
    minWidth: 0,
    fontSize: '0.78rem',
    padding: '0.15rem 0.25rem',
    border: '1px solid #ccc',
    borderRadius: 2,
    boxSizing: 'border-box',
    textAlign: 'right',
  };
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.2rem',
        width: '100%',
      }}
    >
      <input
        type="number"
        min={0}
        max={150}
        value={props.min}
        onChange={(e) => props.onChange('min', e.target.value)}
        placeholder="min"
        aria-label="Minimum age"
        style={inputStyle}
      />
      <span style={{ color: '#888', fontSize: '0.78rem' }}>-</span>
      <input
        type="number"
        min={0}
        max={150}
        value={props.max}
        onChange={(e) => props.onChange('max', e.target.value)}
        placeholder="max"
        aria-label="Maximum age"
        style={inputStyle}
      />
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
  // Long values (service names like "MRI BRAIN WITHOUT CONTRAST W AND
  // WO CONTRAST") need to wrap rather than truncate so the user
  // doesn't lose information. Keep the label · value pair as a single
  // unit but allow it to break across lines within its row.
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
