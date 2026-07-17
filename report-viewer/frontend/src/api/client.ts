// Single thin fetch wrapper. The SPA is served from the same origin as
// the FastAPI backend (via the /spa/ StaticFiles mount), so absolute URLs
// like /api/searches resolve correctly and the oauth2-proxy session
// cookie rides along automatically (credentials: 'same-origin' is the
// default). The backend reads identity from the oauth2-proxy headers
// Traefik injects at the ingress, so no bearer plumbing is needed here.

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

// Never surfaces the raw FastAPI `detail` (can leak SQL fragments / stack
// remnants). Status code stays as small print for support triage.
export function friendlyError(err: unknown, subject: string): string {
  if (!(err instanceof ApiError)) {
    return `Couldn't reach the report-viewer service. Check your connection or try again in a moment.`;
  }
  switch (err.status) {
    case 401:
    case 403:
      return `Your session has expired, or you don't have access to ${subject}. Refresh the page to sign back in.`;
    case 404:
      return `${subject ? subject[0].toUpperCase() + subject.slice(1) : 'It'} couldn't be found.`;
    default:
      return `Something went wrong loading ${subject}. Try again in a moment; if it keeps happening, let the Scout team know.`;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!resp.ok) {
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      try {
        body = await resp.text();
      } catch {
        /* keep null */
      }
    }
    const detail =
      (body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : null) ?? resp.statusText;
    throw new ApiError(resp.status, detail, body);
  }
  return (await resp.json()) as T;
}

export interface SearchMeta {
  id: string;
  id_column: string;
  count: number | null;
  sql: string;
  owner_sub: string;
  created_at: string;
  match_terms: string[];
  match_diagnoses: string[];
  sql_explanation: string;
  owui_chat_id: string;
}

export function listSearches(): Promise<SearchMeta[]> {
  return api<SearchMeta[]>('/api/searches');
}

export interface AppConfig {
  chatOrigin: string;
}

export function getConfig(): Promise<AppConfig> {
  return api<AppConfig>('/api/config');
}

export interface RowsResponse {
  id: string;
  page: number;
  limit: number;
  total: number;
  columns: string[];
  rows: Array<Record<string, unknown>>;
}

export function getSearch(searchId: string): Promise<SearchMeta> {
  return api<SearchMeta>(`/api/searches/${encodeURIComponent(searchId)}`);
}

export interface RowsParams {
  page: number;
  limit: number;
  sort?: { col: string; dir: 'asc' | 'desc' } | null;
  filters?: FilterState;
}

export interface FilterState {
  patient_age?: { min?: string; max?: string };
  message_dt?: { min?: string; max?: string };
  sex?: string[];
  modality?: string[];
  service_name?: string;
  epic_mrn?: string;
  accession_number?: string;
  sending_facility?: string;
}

export function activeFilterCount(f: FilterState): number {
  let n = 0;
  if (f.patient_age && (f.patient_age.min || f.patient_age.max)) n++;
  if (f.message_dt && (f.message_dt.min || f.message_dt.max)) n++;
  if (f.sex && f.sex.length > 0) n++;
  if (f.modality && f.modality.length > 0) n++;
  if (f.service_name && f.service_name.length > 0) n++;
  if (f.epic_mrn && f.epic_mrn.length > 0) n++;
  if (f.accession_number && f.accession_number.length > 0) n++;
  if (f.sending_facility && f.sending_facility.length > 0) n++;
  return n;
}

export interface ReportDetail {
  source_file: string | null;
  message_control_id: string | null;
  accession_number: string | null;
  epic_mrn: string | null;
  resolved_epic_mrn: string | null;
  mpi: string | null;
  resolved_mpi: string | null;
  message_dt: string | null;
  modality: string | null;
  service_name: string | null;
  sending_facility: string | null;
  diagnostic_service_id: string | null;
  patient_age: number | null;
  sex: string | null;
  race: string | null;
  ethnic_group: string | null;
  birth_date: string | null;
  requested_dt: string | null;
  observation_dt: string | null;
  observation_end_dt: string | null;
  results_report_status_change_dt: string | null;
  report_status: string | null;
  study_instance_uid: string | null;
  principal_result_interpreter: unknown;
  assistant_result_interpreter: unknown;
  technician: unknown;
  report_text: string | null;
  report_section_impression: string | null;
  report_section_findings: string | null;
  report_section_addendum: string | null;
  diagnoses: Array<Record<string, unknown>> | null;
}

// Shares /api/reports/read with the OWUI scout_get_reports tool.
// Row visibility is enforced by OPA at Trino; no app-side cohort check.
export async function getReport(reportId: string, idColumn: string): Promise<ReportDetail> {
  const resp = await api<{ columns: string[]; rows: Array<Record<string, unknown>> }>(
    '/api/reports/read',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [reportId], id_column: idColumn }),
    },
  );
  const row = resp.rows[0] ?? {};
  // reports_latest exposes the lake file path as `primary_report_identifier`;
  // the frontend refers to it as `source_file`.
  if (row.primary_report_identifier !== undefined && row.source_file === undefined) {
    row.source_file = row.primary_report_identifier;
  }
  return row as unknown as ReportDetail;
}

export function getSearchRows(searchId: string, params: RowsParams): Promise<RowsResponse> {
  const qs = new URLSearchParams();
  qs.set('page', String(params.page));
  qs.set('limit', String(params.limit));
  if (params.sort) {
    qs.set('sort', `${params.sort.col}:${params.sort.dir}`);
  }
  const f = params.filters;
  if (f) {
    if (f.patient_age?.min) qs.set('filter.patient_age.min', f.patient_age.min);
    if (f.patient_age?.max) qs.set('filter.patient_age.max', f.patient_age.max);
    if (f.message_dt?.min) qs.set('filter.message_dt.min', f.message_dt.min);
    if (f.message_dt?.max) qs.set('filter.message_dt.max', f.message_dt.max);
    for (const v of f.sex ?? []) qs.append('filter.sex', v);
    for (const v of f.modality ?? []) qs.append('filter.modality', v);
    if (f.service_name) qs.set('filter.service_name', f.service_name);
    if (f.epic_mrn) qs.set('filter.epic_mrn', f.epic_mrn);
    if (f.accession_number) qs.set('filter.accession_number', f.accession_number);
    if (f.sending_facility) qs.set('filter.sending_facility', f.sending_facility);
  }
  return api<RowsResponse>(`/api/searches/${encodeURIComponent(searchId)}/rows?${qs.toString()}`);
}
