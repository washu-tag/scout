// Single thin fetch wrapper. The SPA is served from the same origin as
// the FastAPI backend (via the /spa/ StaticFiles mount), so absolute URLs
// like /datasets resolve correctly and the oauth2-proxy session cookie
// rides along automatically (credentials: 'same-origin' is the default).
// No bearer plumbing is needed in V1 — auth.py's Path 2 picks up the
// oauth2-proxy headers injected at the ingress.

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
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
  // Some endpoints (export.csv) aren't JSON, but those aren't called via
  // this wrapper — they're navigated to directly.
  return (await resp.json()) as T;
}

export interface DatasetMeta {
  dataset_id: string;
  kind: string;
  id_column: string;
  count: number;
  parent_id: string | null;
  source_sql: string;
  owner_sub: string;
  created_at: string;
  expires_at: string;
  last_read_at: string;
  highlight_terms: string[];
  sql_explanation: string;
  owui_chat_id: string;
  owui_chat_title: string;
}

export function listDatasets(): Promise<DatasetMeta[]> {
  return api<DatasetMeta[]>('/api/datasets');
}

export interface RowsResponse {
  dataset_id: string;
  page: number;
  limit: number;
  total: number;
  columns: string[];
  rows: Array<Record<string, unknown>>;
}

// Detail page calls capability-auth endpoints (dataset_id IS the cap)
// instead of /api/datasets/{id}* because the page is also loaded inside
// the OWUI chat iframe, where the user identity isn't always available
// (oauth2-proxy gates the datasets host but not the chat-host alias).
// /api/datasets/{id}* are owner-scoped — fine for the SPA homepage
// where the user is logged in, but they'd 401 inside the iframe.
export function getDataset(datasetId: string): Promise<DatasetMeta> {
  return api<DatasetMeta>(`/datasets/${encodeURIComponent(datasetId)}`);
}

export interface RowsParams {
  page: number;
  limit: number;
  // Server-side sort. e.g. { col: 'message_dt', dir: 'desc' }
  sort?: { col: string; dir: 'asc' | 'desc' } | null;
  // Server-side filters keyed by column name → substring match.
  filters?: Record<string, string>;
}

export interface ReportDetail {
  message_control_id: string | null;
  accession_number: string | null;
  epic_mrn: string | null;
  mpi: string | null;
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

// On-the-fly full-report fetch. Dataset-scoped — the backend verifies
// the report is in the cohort before returning text. Same endpoint
// powers the future read_reports LLM tool (single or batched).
export function getReport(datasetId: string, reportId: string): Promise<ReportDetail> {
  return api<ReportDetail>(
    `/datasets/${encodeURIComponent(datasetId)}/reports/${encodeURIComponent(reportId)}`,
  );
}

export function getDatasetRows(datasetId: string, params: RowsParams): Promise<RowsResponse> {
  const qs = new URLSearchParams();
  qs.set('page', String(params.page));
  qs.set('limit', String(params.limit));
  if (params.sort) {
    qs.set('sort', `${params.sort.col}:${params.sort.dir}`);
  }
  for (const [col, val] of Object.entries(params.filters ?? {})) {
    if (val) qs.set(`filter.${col}`, val);
  }
  return api<RowsResponse>(`/datasets/${encodeURIComponent(datasetId)}/rows?${qs.toString()}`);
}
