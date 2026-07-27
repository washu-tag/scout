import type { FilterState } from './api/client';

let _chatOrigin = '';

export function setChatOrigin(origin: string): void {
  _chatOrigin = origin;
}

export function chatOrigin(): string {
  return _chatOrigin;
}

export function chatUrl(chatId: string): string {
  return `${_chatOrigin}/c/${encodeURIComponent(chatId)}`;
}

// `input:prompt` not `:submit`: OWUI's auto-submit requires real
// same-origin between iframe and chat, which Scout's dedicated
// subdomain doesn't satisfy. Filling the composer avoids the
// per-click confirmation dialog.
export function submitChatPrompt(text: string): void {
  if (window.parent === window || !_chatOrigin) return;
  window.parent.postMessage({ type: 'input:prompt', text }, _chatOrigin);
}

export function buildFilterPrompt(searchId: string, filters: FilterState): string | null {
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
  if (filters.epic_mrn) {
    clauses.push(`epic_mrn contains "${filters.epic_mrn}"`);
  }
  if (filters.accession_number) {
    clauses.push(`accession_number contains "${filters.accession_number}"`);
  }
  if (filters.sending_facility) {
    clauses.push(`sending_facility contains "${filters.sending_facility}"`);
  }
  if (clauses.length === 0) return null;
  return `Refine search ${searchId}. Filter rows where ${clauses.join(', ')}.`;
}

export function buildDiscussPrompt(sourceFile: string): string {
  return `Read the report at \`${sourceFile}\`. Walk me through the findings, impression, and key diagnoses.`;
}
