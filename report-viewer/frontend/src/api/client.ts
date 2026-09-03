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
  columns: string[];
  rows: Array<Record<string, unknown>>;
  total: number;
  truncated: boolean;
}

export function getSearch(searchId: string): Promise<SearchMeta> {
  return api<SearchMeta>(`/api/searches/${encodeURIComponent(searchId)}`);
}

export interface FilterState {
  patient_age?: { min?: string; max?: string };
  message_dt?: { min?: string; max?: string };
  sex?: string[];
  modality?: string[];
  service_name?: string;
  epic_mrn?: string;
  patient_mpi?: string;
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
  if (f.patient_mpi && f.patient_mpi.length > 0) n++;
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

// The whole cohort in one request (lean columns, capped server-side); the SPA
// sorts/filters/paginates it client-side. Report text loads per-row on expand.
export function getSearchRows(searchId: string): Promise<RowsResponse> {
  return api<RowsResponse>(`/api/searches/${encodeURIComponent(searchId)}/rows`);
}

export type PlotDetail = {
  id: string;
  spec: Record<string, unknown>;
  rows: Array<Record<string, unknown>>;
  sql: string;
  sql_explanation: string;
  truncated: boolean;
};

export function getPlot(plotId: string): Promise<PlotDetail> {
  return api<PlotDetail>(`/api/plots/${encodeURIComponent(plotId)}`);
}

export interface PlotMeta {
  id: string;
  sql: string;
  owner_sub: string;
  created_at: string;
  sql_explanation: string;
  owui_chat_id: string;
}

export function listPlots(): Promise<PlotMeta[]> {
  return api<PlotMeta[]>('/api/plots');
}

// Filters the in-memory cohort: text = case-insensitive substring, multi =
// membership, range = inclusive (message_dt compared on its YYYY-MM-DD prefix).
export function filterRows(
  rows: Array<Record<string, unknown>>,
  f: FilterState,
): Array<Record<string, unknown>> {
  const svc = f.service_name?.trim().toLowerCase() || null;
  const mrn = f.epic_mrn?.trim().toLowerCase() || null;
  const pmpi = f.patient_mpi?.trim().toLowerCase() || null;
  const acc = f.accession_number?.trim().toLowerCase() || null;
  const fac = f.sending_facility?.trim().toLowerCase() || null;
  const sexSet = f.sex && f.sex.length ? new Set(f.sex) : null;
  const modSet = f.modality && f.modality.length ? new Set(f.modality) : null;
  const ageMin = f.patient_age?.min ? Number(f.patient_age.min) : null;
  const ageMax = f.patient_age?.max ? Number(f.patient_age.max) : null;
  const dtMin = f.message_dt?.min || null;
  const dtMax = f.message_dt?.max || null;
  const has = (v: unknown, q: string) =>
    String(v ?? '')
      .toLowerCase()
      .includes(q);
  return rows.filter((r) => {
    if (svc && !has(r.service_name, svc)) return false;
    if (mrn && !has(r.epic_mrn, mrn)) return false;
    if (pmpi && !has(r.patient_mpi, pmpi)) return false;
    if (acc && !has(r.accession_number, acc)) return false;
    if (fac && !has(r.sending_facility, fac)) return false;
    if (sexSet && !sexSet.has(String(r.sex))) return false;
    if (modSet && !modSet.has(String(r.modality))) return false;
    if (ageMin !== null || ageMax !== null) {
      const raw = r.patient_age;
      if (raw == null || raw === '') return false;
      const a = Number(raw);
      if (!Number.isFinite(a)) return false;
      if (ageMin !== null && a < ageMin) return false;
      if (ageMax !== null && a > ageMax) return false;
    }
    if (dtMin || dtMax) {
      const raw = r.message_dt;
      if (raw == null || raw === '') return false; // no date can't match a range
      const d = String(raw).slice(0, 10);
      if (dtMin && d < dtMin) return false;
      if (dtMax && d > dtMax) return false;
    }
    return true;
  });
}

// CSV cell with a spreadsheet formula-injection guard (mirrors the server
// export): wrap in quotes, escape embedded quotes, and prefix a leading
// =/+/-/@ or control char with a single quote so it isn't evaluated.
function csvCell(v: unknown): string {
  let s = v == null ? '' : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

export function rowsToCsv(columns: string[], rows: Array<Record<string, unknown>>): string {
  const header = columns.map(csvCell).join(',');
  const body = rows.map((r) => columns.map((c) => csvCell(r[c])).join(',')).join('\r\n');
  return body ? `${header}\r\n${body}` : header;
}

// Builds and downloads the CSV entirely client-side from the in-memory rows -
// no server round-trip. Caller passes the current filtered+sorted set.
export function downloadCsv(
  filename: string,
  columns: string[],
  rows: Array<Record<string, unknown>>,
): void {
  const blob = new Blob([rowsToCsv(columns, rows)], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
