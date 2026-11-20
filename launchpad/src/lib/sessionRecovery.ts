import { REAUTH_REQUIRED } from './keycloakToken';

/**
 * What the launchpad shell does when its session check finds Scout's session over.
 *
 * - `reload`: oauth2-proxy answered 401. Reloading lands on oauth2-proxy's own sign-in page,
 *   which starts nothing until it is clicked, so it is safe even with nobody at the screen.
 * - `ask`: a load balancer's OIDC sign-in redirected, or the launchpad reported its own session
 *   gone (440). Either way, going to sign-in starts an attempt that expires within minutes, so a
 *   page left on it fails when finally used. The shell waits for a click instead.
 */
export type SessionRecovery = 'none' | 'reload' | 'ask';

export function sessionRecovery(response: Pick<Response, 'status' | 'type'>): SessionRecovery {
  if (response.status === 401) return 'reload';
  if (response.type === 'opaqueredirect' || response.status === REAUTH_REQUIRED) return 'ask';
  return 'none';
}

/** After the click: the edge's sign-in when the edge session is gone, otherwise the launchpad's. */
export function edgeSessionGone(response: Pick<Response, 'status' | 'type'>): boolean {
  return response.status === 401 || response.type === 'opaqueredirect';
}

/**
 * What the shell does with next-auth's session. A refetch that fails reports no session, and
 * next-auth refetches in every open tab when another tab loads, so once the shell has had a
 * session a missing one is checked against `/api/session` while the shell and its apps stay.
 *
 * - `wait`: the first session is still loading.
 * - `sign-in`: there never was a session; sign in at once.
 * - `shell`: render the shell.
 * - `verify`: render the shell and check `/api/session`, which decides whether it ended.
 */
export type SessionGate = 'wait' | 'sign-in' | 'shell' | 'verify';

export function sessionGate(
  status: 'loading' | 'authenticated' | 'unauthenticated',
  hasSession: boolean,
  hadSession: boolean,
): SessionGate {
  if (hasSession) return 'shell';
  if (hadSession) return 'verify';
  return status === 'loading' ? 'wait' : 'sign-in';
}
