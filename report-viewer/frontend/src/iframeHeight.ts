// Iframe height resize via {type:'iframe:height'} postMessage, picked
// up by OWUI's FullHeightIframe.svelte. Cross-origin to chat.

import { chatOrigin } from './chat';

export const HEIGHT_COMPACT = 500;
export const HEIGHT_EXPANDED = 850;

let current = HEIGHT_COMPACT;

export function getHeight(): number {
  return current;
}

export function postHeight(): void {
  if (window.parent === window) return;
  const origin = chatOrigin();
  if (!origin) return;
  window.parent.postMessage({ type: 'iframe:height', height: current }, origin);
}

export function setHeight(px: number): void {
  current = px;
  postHeight();
}
