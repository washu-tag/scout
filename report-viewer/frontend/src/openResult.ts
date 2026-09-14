import { useCallback, useState } from 'react';

export interface OpenResultState {
  opening: boolean;
  error: string | null;
  copiedLink: string | null;
  copyFailed: boolean;
}

/** Open a result URL from any action button, reusably.
 *
 * Always attempts a real navigation via a simulated anchor click, not
 * `window.open()` - Chrome treats a script-controlled popup more strictly
 * than a real navigation and can block it outright with
 * ERR_BLOCKED_BY_RESPONSE / "blocked from loading in a popup opened by a
 * sandboxed iframe" (relevant when report-viewer is embedded in OWUI's
 * chat). A blocked popup doesn't throw a catchable error - the browser
 * silently drops it - so there's no reliable signal to decide whether a
 * fallback is needed; always also copy the link and expose it, so the
 * caller can render an "in case that didn't open" affordance
 * unconditionally.
 *
 * Whether the popup actually succeeds is controlled by the destination's
 * own Cross-Origin-Opener-Policy response header (e.g.
 * popup-friendly-security-headers in
 * ansible/roles/traefik/tasks/main.yaml), not anything this hook does -
 * a destination that hasn't opted into that header will always fall
 * through to the copy-link affordance when embedded.
 */
export function useOpenResult() {
  const [state, setState] = useState<OpenResultState>({
    opening: false,
    error: null,
    copiedLink: null,
    copyFailed: false,
  });

  const open = useCallback(async (url: string) => {
    setState({ opening: true, error: null, copiedLink: null, copyFailed: false });
    try {
      const link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      let copyFailed = false;
      try {
        await navigator.clipboard.writeText(url);
      } catch {
        // Sandboxed iframes often lack allow-clipboard-write. Still
        // expose the link below, just don't claim it was copied.
        copyFailed = true;
      }
      setState({ opening: false, error: null, copiedLink: url, copyFailed });
    } catch (err) {
      setState({
        opening: false,
        error: err instanceof Error ? err.message : 'Failed to open link',
        copiedLink: null,
        copyFailed: false,
      });
    }
  }, []);

  return { ...state, open };
}
