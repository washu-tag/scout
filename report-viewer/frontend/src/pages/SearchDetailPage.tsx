import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  useReactTable,
  type SortingState,
  type ExpandedState,
  type VisibilityState,
  type PaginationState,
} from '@tanstack/react-table';
import {
  activeFilterCount,
  downloadCsv,
  filterRows,
  friendlyError,
  getSearch,
  getSearchRows,
  type FilterState,
} from '../api/client';
import { HEIGHT_COMPACT, HEIGHT_EXPANDED, setHeight as setIframeHeight } from '../iframeHeight';
import { buildFilterPrompt } from '../chat';
import { useChatPrompt } from '../ChatPrompt';
import { RowDetail } from './searchDetail/RowDetail';
import { FiltersModal } from './searchDetail/FiltersModal';
import { ExplainSqlModal } from './searchDetail/ExplainSqlModal';
import { ContractIcon, ExpandIcon } from './searchDetail/icons';
import { fmtCell, fmtDate } from './searchDetail/format';
import { ColumnProfileRow } from './searchDetail/ColumnProfileRow';
import { ROW_ACTIVE_BG, DETAIL_ZONE_BG, paginationBtn } from './searchDetail/styles';

const COLUMNS_CONFIG: Array<{
  field: string;
  title: string;
  width: number;
  defaultHidden?: boolean;
  align?: 'right' | 'center';
  mono?: boolean;
  kind?: 'date';
}> = [
  { field: 'epic_mrn', title: 'Epic MRN', width: 80, mono: true },
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
  { field: 'accession_number', title: 'Accession', width: 85, mono: true },
  { field: 'message_dt', title: 'Date', width: 100, kind: 'date' },
  { field: 'modality', title: 'Modality', width: 60 },
  { field: 'service_name', title: 'Service', width: 180 },
  { field: 'sending_facility', title: 'Facility', width: 120, defaultHidden: true },
  { field: 'patient_age', title: 'Age', width: 50, align: 'right' },
  { field: 'sex', title: 'Sex', width: 40, align: 'center' },
  { field: 'evidence', title: 'Label', width: 110, defaultHidden: true },
];

type Row = Record<string, unknown>;

const columnHelper = createColumnHelper<Row>();

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

  // One fetch of the whole cohort; sort/filter/paginate happen client-side.
  const rowsQ = useQuery({
    queryKey: ['search', searchId, 'rows'],
    queryFn: () => getSearchRows(searchId),
    enabled: !!searchId,
  });

  // Expansion is keyed by row id (primary_report_identifier); clear on a new
  // cohort fetch so a fresh search doesn't inherit stale expansions.
  useEffect(() => {
    setExpanded({});
  }, [rowsQ.data]);

  // Reveal the hidden patient_mpi column for legacy cohorts whose only
  // identifier is the mpi (rows with an mpi but no epic_mrn).
  const autoMpiSearchRef = useRef<string | null>(null);
  useEffect(() => {
    const rows = rowsQ.data?.rows;
    if (!rows || autoMpiSearchRef.current === searchId) return;
    autoMpiSearchRef.current = searchId;
    const blank = (v: unknown) => v == null || v === '';
    if (rows.some((r) => blank(r.epic_mrn) && !blank(r.patient_mpi))) {
      setColumnVisibility((v) => ({ ...v, patient_mpi: true }));
    }
  }, [rowsQ.data, searchId]);

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

  const available = useMemo<string[]>(
    () => rowsQ.data?.columns ?? (rowsQ.data?.rows?.[0] ? Object.keys(rowsQ.data.rows[0]) : []),
    [rowsQ.data],
  );

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
          cell: (info) => (c.kind === 'date' ? fmtDate(info.getValue()) : fmtCell(info.getValue())),
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
    state: { sorting, expanded, columnVisibility, pagination },
    onSortingChange: (updater) => {
      setSorting(updater);
      setPagination((p) => ({ ...p, pageIndex: 0 }));
    },
    onExpandedChange: setExpanded,
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
    // Stable id so an expanded row tracks the right report across
    // client-side sort/filter/paginate.
    getRowId: (row: Row, index) =>
      row.primary_report_identifier != null ? String(row.primary_report_identifier) : String(index),
    getRowCanExpand: () => true,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    defaultColumn: { minSize: 40 },
  });

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
        <span style={{ flex: 1 }} />
        {rowsQ.data && (
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
        )}
      </div>
      {rowsQ.error && (
        <p style={{ color: 'var(--rv-danger)' }}>{friendlyError(rowsQ.error, 'these rows')}</p>
      )}
      {!rowsQ.data && rowsQ.isLoading ? (
        <p style={{ color: 'var(--rv-muted)' }}>Loading reports…</p>
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
            {rowsQ.data.truncated && (
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
                Showing the first {rowsQ.data.total.toLocaleString()} rows. Refine your search to
                narrow the cohort.
              </div>
            )}
            <div
              style={{
                overflowX: 'auto',
                overflowY: 'auto',
                flex: '1 1 auto',
                minHeight: 0,
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
                  <ColumnProfileRow
                    columns={table.getVisibleLeafColumns()}
                    rows={data}
                    dateFields={dateFields}
                    stickyTop={headerHeight}
                  />
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => {
                    const isExpanded = row.getIsExpanded();
                    return (
                      <React.Fragment key={row.id}>
                        <tr
                          className={isExpanded ? undefined : 'scout-row'}
                          onClick={() => row.toggleExpanded()}
                          style={{
                            borderBottom: '1px solid var(--rv-border)',
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
                            <td colSpan={row.getVisibleCells().length} style={{ padding: 0 }}>
                              <div style={{ padding: '0.75rem 1rem' }}>
                                <RowDetail
                                  row={row.original}
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
                  {table.getRowModel().rows.length === 0 && (
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
                onClick={() => table.previousPage()}
                disabled={!table.getCanPreviousPage()}
                style={paginationBtn}
              >
                Prev
              </button>
              <span style={{ whiteSpace: 'nowrap' }}>
                {pageIndex + 1} / {lastPage}
              </span>
              <button
                type="button"
                onClick={() => table.nextPage()}
                disabled={!table.getCanNextPage()}
                style={paginationBtn}
              >
                Next
              </button>
              <span
                style={{ marginLeft: '0.4rem', color: 'var(--rv-muted)', whiteSpace: 'nowrap' }}
              >
                Per page:
              </span>
              <select
                value={pagination.pageSize}
                onChange={(e) => table.setPageSize(Number(e.target.value))}
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
                  border: '2px solid var(--rv-border)',
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
