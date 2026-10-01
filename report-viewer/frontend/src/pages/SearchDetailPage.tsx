import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  useReactTable,
  type SortingState,
  type VisibilityState,
  type PaginationState,
} from '@tanstack/react-table';
import {
  activeFilterCount,
  downloadCsv,
  filterRows,
  friendlyError,
  getSearch,
  getSearchProgress,
  getSearchRows,
  newProgressId,
  type FilterState,
} from '../api/client';
import { HEIGHT_COMPACT, HEIGHT_EXPANDED, setHeight as setIframeHeight } from '../iframeHeight';
import { buildFilterPrompt } from '../chat';
import { useChatPrompt } from '../ChatPrompt';
import { LoadingSpinner, QueryProgressInline, useLoadingProgress } from '../QueryProgress';
import { EvidenceFilterChips } from './searchDetail/EvidenceFilterChips';
import { FiltersModal } from './searchDetail/FiltersModal';
import { ExplainSqlModal } from './searchDetail/ExplainSqlModal';
import { ContractIcon, ExpandIcon } from './searchDetail/icons';
import { fmtCell, fmtDate } from './searchDetail/format';
import { ColumnProfileRow } from './searchDetail/ColumnProfileRow';
import { EvidenceCell } from './searchDetail/EvidenceCell';
import { hasEvidence } from './searchDetail/evidenceStats';
import { ReviewPanel } from './searchDetail/ReviewPanel';
import { ROW_ACTIVE_BG, compactBtn, paginationBtn } from './searchDetail/styles';

const COLUMNS_CONFIG: Array<{
  field: string;
  title: string;
  width: number;
  defaultHidden?: boolean;
  align?: 'right' | 'center';
  mono?: boolean;
  kind?: 'date' | 'evidence';
}> = [
  { field: 'accession_number', title: 'Accession', width: 76, mono: true },
  { field: 'epic_mrn', title: 'Epic MRN', width: 80, mono: true, defaultHidden: true },
  {
    field: 'resolved_epic_mrn',
    title: 'Resolved MRN',
    width: 100,
    mono: true,
    defaultHidden: true,
  },
  { field: 'patient_mpi', title: 'Patient MPI', width: 90, mono: true, defaultHidden: true },
  {
    field: 'resolved_mpi',
    title: 'Resolved MPI',
    width: 100,
    mono: true,
    defaultHidden: true,
  },
  { field: 'message_dt', title: 'Date', width: 118, kind: 'date' },
  { field: 'modality', title: 'Modality', width: 48 },
  { field: 'service_name', title: 'Service', width: 148 },
  { field: 'sending_facility', title: 'Facility', width: 120, defaultHidden: true },
  { field: 'patient_age', title: 'Age', width: 50, align: 'right', defaultHidden: true },
  { field: 'sex', title: 'Sex', width: 40, align: 'center', defaultHidden: true },
  { field: 'evidence', title: 'Label', width: 110, defaultHidden: true },
  { field: 'ev_source', title: 'Matched on', width: 248, kind: 'evidence' },
];

type Row = Record<string, unknown>;

const columnHelper = createColumnHelper<Row>();

// Lets the empty table render its headers before the first fetch returns.
const DEFAULT_COLUMNS = COLUMNS_CONFIG.filter((c) => !c.defaultHidden).map((c) => c.field);

// Least self-evidencing first: a code-only row has no text in the report to
// check against, while a text match shows the reviewer its own phrase.
const REVIEW_ORDER = ['diagnosis_code', 'text', 'text_and_code'];

/** The phrase the chip shows, so equal evidence sorts together. */
function evidenceText(row: Row): string {
  return String(
    row.ev_negative_span || row.ev_positive_span || row.ev_dx_codes || '',
  ).toLowerCase();
}

function reviewRank(row: Row): number {
  if (row.ev_negative_span) return 0;
  // Unknown outranks everything explained: we could not say why it is here.
  const source = String(row.ev_source ?? '');
  if (!source) return 1;
  const i = REVIEW_ORDER.indexOf(source);
  return 2 + (i === -1 ? REVIEW_ORDER.length : i);
}

export default function SearchDetailPage() {
  const { searchId = '' } = useParams<{ searchId: string }>();
  const requestPrompt = useChatPrompt();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 100 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [appliedFilters, setAppliedFilters] = useState<FilterState>({});
  const [filtersModalOpen, setFiltersModalOpen] = useState(false);
  const [sqlModalOpen, setSqlModalOpen] = useState(false);
  const [colPickerOpen, setColPickerOpen] = useState(false);
  const colPickerRef = useRef<HTMLDivElement>(null);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() =>
    Object.fromEntries(COLUMNS_CONFIG.filter((c) => c.defaultHidden).map((c) => [c.field, false])),
  );
  const [iframeExpanded, setIframeExpanded] = useState(false);
  const [reviewAt, setReviewAt] = useState<number | null>(null);
  const appliedFiltersKey = useMemo(() => JSON.stringify(appliedFilters), [appliedFilters]);

  const meta = useQuery({
    queryKey: ['search', searchId],
    queryFn: () => getSearch(searchId),
    enabled: !!searchId,
  });

  // One fetch of the whole cohort; sort/filter/paginate happen client-side.
  const ROWS_KEY = ['search', searchId, 'rows'];
  // Set per attempt so a retry polls its own progress, not the attempt it replaced.
  const progressId = useRef('');
  const rowsQ = useQuery({
    queryKey: ROWS_KEY,
    queryFn: ({ signal }) => {
      progressId.current = newProgressId();
      return getSearchRows(searchId, progressId.current, signal);
    },
    enabled: !!searchId,
  });

  // Cancelling aborts the fetch, which the server turns into a Trino cancel.
  const queryClient = useQueryClient();
  const [cancelled, setCancelled] = useState(false);

  const fetchProgress = useCallback(
    () =>
      progressId.current ? getSearchProgress(searchId, progressId.current) : Promise.resolve({}),
    [searchId],
  );
  const loadingState = useLoadingProgress(!rowsQ.data && rowsQ.isLoading, fetchProgress);
  const showLoading = loadingState.show;

  // A fresh cohort must not inherit a selection or an open reader.
  useEffect(() => {
    setReviewAt(null);
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

  const available = useMemo<string[]>(() => rowsQ.data?.columns ?? DEFAULT_COLUMNS, [rowsQ.data]);

  // Distinct modalities present in the loaded cohort, for the filter dialog -
  // derived client-side from the full result set (no separate endpoint).
  const modalityOptions = useMemo(() => {
    const set = new Set<string>();
    for (const r of rowsQ.data?.rows ?? []) {
      const v = r.modality;
      if (v != null && v !== '') set.add(String(v));
    }
    return Array.from(set).sort();
  }, [rowsQ.data]);

  // The profile row sticks below the header, so it needs the header's actual
  // rendered height. A callback ref, not an effect, since the table only
  // renders once rows arrive and an effect keyed on mount would find no
  // header yet. Floored so a fractional height rounds down to a slight
  // overlap rather than up to a visible gap.
  const [headerHeight, setHeaderHeight] = useState(28);
  const headerObserver = useRef<ResizeObserver | null>(null);
  const headerRowRef = useCallback((el: HTMLTableRowElement | null) => {
    headerObserver.current?.disconnect();
    if (!el) return;
    const measure = () => setHeaderHeight(Math.floor(el.getBoundingClientRect().height));
    measure();
    headerObserver.current = new ResizeObserver(measure);
    headerObserver.current.observe(el);
  }, []);

  const dateFields = useMemo(
    () => new Set(COLUMNS_CONFIG.filter((c) => c.kind === 'date').map((c) => c.field)),
    [],
  );

  const columns = useMemo(
    () =>
      COLUMNS_CONFIG.filter((c) => available.includes(c.field)).map((c) =>
        columnHelper.accessor((row: Row) => row[c.field], {
          id: c.field,
          header: c.title,
          size: c.width,
          cell: (info) => {
            if (c.kind === 'date') return fmtDate(info.getValue());
            if (c.kind === 'evidence') return <EvidenceCell row={info.row.original} />;
            return fmtCell(info.getValue());
          },
          sortingFn:
            c.kind === 'evidence'
              ? (a, b) =>
                  reviewRank(a.original) - reviewRank(b.original) ||
                  evidenceText(a.original).localeCompare(evidenceText(b.original))
              : 'auto',
          meta: { align: c.align, mono: c.mono },
        }),
      ),
    [available],
  );

  // Filter the full in-memory cohort in fetch order (the order the search SQL
  // returned) so the initial view preserves the LLM's ORDER BY; TanStack then
  // sorts/paginates on demand.
  const data = useMemo(
    () => filterRows(rowsQ.data?.rows ?? [], appliedFilters),
    [rowsQ.data, appliedFiltersKey],
  );

  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnVisibility, pagination },
    onSortingChange: (updater) => {
      setSorting(updater);
      setPagination((p) => ({ ...p, pageIndex: 0 }));
    },
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
    // Stable id so selection survives client-side sort/filter/paginate.
    getRowId: (row: Row, index) =>
      row.primary_report_identifier != null ? String(row.primary_report_identifier) : String(index),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    defaultColumn: { minSize: 40 },
  });

  // Sorted, not paginated: the reader walks the whole filtered cohort.
  const queue = table.getSortedRowModel().rows;
  const pageSize = pagination.pageSize;

  // Follow the reader, so closing the panel lands where they stopped.
  const goToReview = (next: number) => {
    setReviewAt(next);
    if (queue[next]) setPagination((p) => ({ ...p, pageIndex: Math.floor(next / pageSize) }));
  };

  const total = data.length;
  const lastPage = table.getPageCount() || 1;
  const pageIndex = table.getState().pagination.pageIndex;

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
        {cancelled && !rowsQ.data ? (
          <>
            <span style={{ color: 'var(--rv-muted)', fontSize: '0.7rem' }}>
              Cancelled · {loadingState.seconds}s
            </span>
            <button
              type="button"
              style={compactBtn}
              onClick={() => {
                setCancelled(false);
                rowsQ.refetch();
              }}
            >
              Retry
            </button>
          </>
        ) : (
          showLoading &&
          !rowsQ.error && (
            <>
              <QueryProgressInline {...loadingState} doneLabel="Reports loaded" />
              {!rowsQ.data && (
                <button
                  type="button"
                  style={compactBtn}
                  onClick={() => {
                    setCancelled(true);
                    queryClient.cancelQueries({ queryKey: ROWS_KEY, exact: true });
                  }}
                >
                  Cancel
                </button>
              )}
            </>
          )
        )}
        <span style={{ flex: 1 }} />
        {
          <span
            title="Search ID"
            style={{
              color: 'var(--rv-muted)',
              fontSize: '0.7rem',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              userSelect: 'all',
            }}
          >
            {searchId}
          </span>
        }
      </div>
      {rowsQ.error && (
        <p style={{ color: 'var(--rv-danger)' }}>{friendlyError(rowsQ.error, 'these rows')}</p>
      )}
      {
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            flex: '1 1 auto',
            minHeight: 0,
          }}
        >
          {rowsQ.data?.truncated && (
            <div
              style={{
                flex: '0 0 auto',
                marginBottom: '0.4rem',
                padding: '0.35rem 0.6rem',
                fontSize: '0.78rem',
                color: 'var(--rv-muted)',
                background: 'var(--rv-surface-2)',
                border: '1px solid var(--rv-border)',
                borderRadius: 4,
              }}
            >
              Showing the first {rowsQ.data?.total.toLocaleString()} rows. Refine your search to
              narrow the cohort.
            </div>
          )}
          {/* Containing block for the review panel. The box inside scrolls,
              so anchoring to it would scroll the panel away with the rows. */}
          <div style={{ position: 'relative', flex: '1 1 auto', minHeight: 0 }}>
            <div
              style={{
                position: 'absolute',
                inset: 0,
                overflowX: 'auto',
                overflowY: 'auto',
                background: 'var(--rv-surface)',
                border: '1px solid var(--rv-border)',
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
                  {table.getHeaderGroups().map((hg, hgIndex) => (
                    <tr key={hg.id} ref={hgIndex === 0 ? headerRowRef : undefined}>
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
                              color: 'var(--rv-muted)',
                              background: 'var(--rv-surface-2)',
                              // border-collapse: collapse + sticky drops
                              // border-bottom on scroll; box-shadow survives.
                              boxShadow: 'inset 0 -1px 0 var(--rv-border)',
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
                                ...(isResizing
                                  ? { borderRight: '2px solid var(--rv-accent)' }
                                  : {}),
                              }}
                            />
                          </th>
                        );
                      })}
                    </tr>
                  ))}
                  {!!rowsQ.data && (
                    <ColumnProfileRow
                      columns={table.getVisibleLeafColumns()}
                      rows={data}
                      dateFields={dateFields}
                      stickyTop={headerHeight}
                    />
                  )}
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => {
                    const active = reviewAt !== null && queue[reviewAt]?.id === row.id;
                    return (
                      <React.Fragment key={row.id}>
                        <tr
                          className={active ? undefined : 'scout-row'}
                          onClick={() => setReviewAt(queue.findIndex((q) => q.id === row.id))}
                          style={{
                            borderBottom: '1px solid var(--rv-border)',
                            cursor: 'pointer',
                            background: active ? ROW_ACTIVE_BG : 'transparent',
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
                      </React.Fragment>
                    );
                  })}
                  {table.getRowModel().rows.length === 0 && !!rowsQ.data && (
                    <tr>
                      <td
                        colSpan={table.getVisibleFlatColumns().length}
                        style={{ padding: '1rem', textAlign: 'center', color: 'var(--rv-muted)' }}
                      >
                        {activeFilterCount(appliedFilters) > 0
                          ? 'No rows match your filters.'
                          : 'No reports in this search.'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {!rowsQ.data && !cancelled && !rowsQ.error && (
                <LoadingSpinner show={showLoading} fill />
              )}
              {!rowsQ.data && cancelled && (
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--rv-muted)',
                    fontSize: '0.8rem',
                  }}
                >
                  Query cancelled. Retry to load these reports.
                </div>
              )}
            </div>
            {reviewAt !== null && queue.length > 0 && (
              <ReviewPanel
                queue={queue.map((r) => r.original)}
                index={Math.min(reviewAt, queue.length - 1)}
                onIndex={goToReview}
                onClose={() => setReviewAt(null)}
              />
            )}
          </div>
          {hasEvidence(rowsQ.data?.rows ?? []) && (
            <EvidenceFilterChips
              rows={rowsQ.data?.rows ?? []}
              filters={appliedFilters}
              onChange={setAppliedFilters}
            />
          )}
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
              onClick={() => table.previousPage()}
              disabled={!rowsQ.data || !table.getCanPreviousPage()}
              style={paginationBtn}
            >
              Prev
            </button>
            <span
              style={{
                whiteSpace: 'nowrap',
                fontVariantNumeric: 'tabular-nums',
                minWidth: 72,
                textAlign: 'center',
              }}
            >
              {pageIndex + 1} / {lastPage}
            </span>
            <button
              type="button"
              onClick={() => table.nextPage()}
              disabled={!rowsQ.data || !table.getCanNextPage()}
              style={paginationBtn}
            >
              Next
            </button>
            <span style={{ marginLeft: '0.4rem', color: 'var(--rv-muted)', whiteSpace: 'nowrap' }}>
              Per page:
            </span>
            <select
              value={pagination.pageSize}
              onChange={(e) => table.setPageSize(Number(e.target.value))}
              disabled={!rowsQ.data}
              style={{ fontSize: '0.85rem' }}
            >
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
            <span
              style={{
                color: 'var(--rv-muted)',
                fontSize: '0.75rem',
                whiteSpace: 'nowrap',
                fontVariantNumeric: 'tabular-nums',
                minWidth: 78,
              }}
            >
              {meta.error
                ? 'Failed to load metadata'
                : rowsQ.data
                  ? `${total.toLocaleString()} rows`
                  : ''}
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
                border: '2px solid var(--rv-border)',
                borderTopColor: '#ea580c',
                animation: 'scoutSpin 0.8s linear infinite',
                display: 'inline-block',
              }}
            />
            <span style={{ flex: 1 }} />
            <button
              type="button"
              disabled={!rowsQ.data}
              onClick={() => setFiltersModalOpen(true)}
              style={
                activeFilterCount(appliedFilters) > 0
                  ? {
                      ...paginationBtn,
                      background: 'var(--rv-accent)',
                      color: '#fff',
                      borderColor: 'var(--rv-accent)',
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
                disabled={!rowsQ.data}
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
                    background: 'var(--rv-surface)',
                    border: '1px solid var(--rv-border)',
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
            <button
              type="button"
              onClick={() => {
                // Always include the unique id so exported rows stay identifiable
                // even if the user hid the id/accession columns.
                const cols = table.getVisibleLeafColumns().map((c) => c.id);
                if (!cols.includes('primary_report_identifier')) {
                  cols.unshift('primary_report_identifier');
                }
                downloadCsv(
                  `${searchId}.csv`,
                  cols,
                  table.getPrePaginationRowModel().rows.map((r) => r.original),
                );
              }}
              disabled={!rowsQ.data}
              style={paginationBtn}
              title="Download the current filtered and sorted rows as CSV"
            >
              Download CSV
            </button>
            <button
              type="button"
              onClick={() => {
                const next = !iframeExpanded;
                setIframeExpanded(next);
                setIframeHeight(next ? HEIGHT_EXPANDED : HEIGHT_COMPACT);
              }}
              title={
                iframeExpanded ? 'Shrink viewer back to compact size' : 'Grow viewer for more room'
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
      }
      {sqlModalOpen && (
        <ExplainSqlModal
          explanation={meta.data?.sql_explanation ?? ''}
          sql={meta.data?.sql ?? ''}
          executedSql={meta.data?.executed_sql ?? ''}
          rows={rowsQ.data?.rows ?? []}
          onFilter={(patch) => {
            setAppliedFilters((f) => ({ ...f, ...patch }));
            setSqlModalOpen(false);
          }}
          onClose={() => setSqlModalOpen(false)}
        />
      )}
      {filtersModalOpen && (
        <FiltersModal
          initial={appliedFilters}
          availableColumns={available}
          modalityOptions={modalityOptions}
          onApply={(next) => {
            setAppliedFilters(next);
            setFiltersModalOpen(false);
          }}
          onRefineInChat={(next) => {
            requestPrompt(buildFilterPrompt(searchId, next), {
              title: 'Filter in Chat?',
              onConfirm: () => {
                setAppliedFilters(next);
                setFiltersModalOpen(false);
              },
            });
          }}
          onClose={() => setFiltersModalOpen(false)}
        />
      )}
    </div>
  );
}
