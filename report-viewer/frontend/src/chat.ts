import { filterClauses, type FilterState } from './api/client';

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
  const clauses = filterClauses(filters);
  if (clauses.length === 0) return null;
  return `Refine search ${searchId}. Filter rows where ${clauses.join(', ')}.`;
}

export function buildDiscussPrompt(sourceFile: string): string {
  return `Read the report at \`${sourceFile}\`. Walk me through the findings, impression, and key diagnoses.`;
}

export function buildDiscussPlotPrompt(chartId: string): string {
  return `Discuss the chart \`${chartId}\`. Pull its data and give me your read: patterns, outliers, anything worth flagging.`;
}
