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
  type FilterState,
} from '../api/client';
import { HEIGHT_COMPACT, HEIGHT_EXPANDED, setHeight as setIframeHeight } from '../iframeHeight';
import { applyFilterToChat } from '../chat';
import { RowDetail } from './searchDetail/RowDetail';
import { FiltersModal } from './searchDetail/FiltersModal';
import { ExplainSqlModal } from './searchDetail/ExplainSqlModal';
import { SendToXnatModal } from './searchDetail/SendToXnatModal';
import { fmtCell, fmtDate } from './searchDetail/format';
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
  { field: 'epic_mrn', title: 'MRN', width: 80, mono: true },
  { field: 'mpi', title: 'MPI', width: 80, mono: true, defaultHidden: true },
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
        <p style={{ color: '#666' }}>Loading reports…</p>
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
                            <td colSpan={row.getVisibleCells().length} style={{ padding: 0 }}>
                              <div style={{ padding: '0.75rem 1rem' }}>
                                <RowDetail
                                  row={row.original}
                                  idColumn={meta.data?.id_column ?? 'primary_report_identifier'}
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
              {/* Send to XNAT is hidden pre-release. The XNAT push isn't wired
                  yet (SendToXnatModal is a stub). See ADR 0026. */}
              {/* <button type="button" onClick={() => setXnatModalOpen(true)} style={paginationBtn}>
                Send to XNAT
              </button> */}
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
          availableColumns={available}
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
