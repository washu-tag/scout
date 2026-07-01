// Maps report-viewer.<host> to chat.<host> for postMessage targeting
// and outbound chat links. Falls through untouched in local dev.
export function chatOrigin(): string {
  return window.location.origin.replace(/\/\/report-viewer\./, '//chat.');
}

export function chatUrl(chatId: string): string {
  return `${chatOrigin()}/c/${encodeURIComponent(chatId)}`;
}
