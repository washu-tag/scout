// Detect whether the SPA is currently loaded inside an iframe. Used to
// strip the standalone-only chrome (banner header, "back to all
// datasets" link, page padding) when embedded in the OWUI chat.
//
// `window.self !== window.top` is the canonical check; works both same-
// origin (where we can also touch window.frameElement) and cross-origin
// (where we can't, but the inequality still holds). Cached at module
// load — the iframe state doesn't change during a session.
export const isEmbedded = (): boolean => {
  try {
    return window.self !== window.top;
  } catch {
    // Cross-origin access can throw on some browsers — if it threw,
    // we're definitely framed.
    return true;
  }
};
