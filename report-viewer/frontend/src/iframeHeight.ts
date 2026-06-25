// Iframe height resize via {type:'iframe:height'} postMessage, picked
// up by OWUI's FullHeightIframe.svelte. Cross-origin to chat.

export const HEIGHT_COMPACT = 500;
export const HEIGHT_EXPANDED = 850;

// Maps report-viewer.<host> to chat.<host> for postMessage targetOrigin.
export function chatOrigin(): string {
  return window.location.origin.replace(/\/\/report-viewer\./, '//chat.');
}

let current = HEIGHT_COMPACT;

export function getHeight(): number {
  return current;
}

export function postHeight(): void {
  if (window.parent === window) return;
  window.parent.postMessage({ type: 'iframe:height', height: current }, chatOrigin());
}

export function setHeight(px: number): void {
  current = px;
  postHeight();
}
