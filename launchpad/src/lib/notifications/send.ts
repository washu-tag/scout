import type { JWTVerifyGetKey } from 'jose';
import { checkRequest, keySchema, sendSchema, type SendRequest } from './content';
import { database } from './db';
import type { Sender } from './registration';
import { loadSenders } from './registry';
import { keycloakRoleDirectory, type RoleDirectory } from './roles';
import { disabledSenders, maxDeliveries, sendRateLimit } from './settings';
import { noticeStore, type NoticeStore } from './store';
import { verifySenderToken } from './token';

const BODY_MAX_BYTES = 128 * 1024;

export interface Reply {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
}

export interface SendDeps {
  issuer: string;
  launchpadClientId: string;
  keys?: JWTVerifyGetKey;
  senders: () => Promise<Map<string, Sender>>;
  store: () => Promise<NoticeStore>;
  roles: RoleDirectory;
  disabled: string[];
  rateLimit: number;
  maxDeliveries: number;
  now?: () => Date;
}

let directory: { issuer: string; roles: RoleDirectory } | null = null;

export function defaultSendDeps(): SendDeps {
  const issuer = process.env.KEYCLOAK_ISSUER ?? '';
  if (directory?.issuer !== issuer) directory = { issuer, roles: keycloakRoleDirectory(issuer) };
  return {
    issuer,
    launchpadClientId: process.env.KEYCLOAK_CLIENT_ID ?? '',
    senders: () => loadSenders(),
    store: async () => noticeStore(await database()),
    roles: directory.roles,
    disabled: disabledSenders(),
    rateLimit: sendRateLimit(),
    maxDeliveries: maxDeliveries(),
  };
}

function fail(
  status: number,
  error: string,
  message: string,
  headers?: Record<string, string>,
): Reply {
  return { status, body: { error, message }, headers };
}

const MESSAGES: Record<string, string> = {
  missing_token: 'send a Bearer token from your client-credentials grant',
  invalid_token: 'the token is not a valid access token from this realm for the launchpad',
  missing_role: 'the token lacks the launchpad client role notifications-send',
  keys_unavailable: "the realm's signing keys could not be fetched; try again",
};

async function authorize(
  authorization: string | null,
  deps: SendDeps,
): Promise<{ sender: Sender; store: NoticeStore } | Reply> {
  const token = await verifySenderToken(authorization, {
    issuer: deps.issuer,
    clientId: deps.launchpadClientId,
    keys: deps.keys,
  });
  if (!token.ok) {
    return fail(
      token.status,
      token.error,
      MESSAGES[token.error],
      token.status === 401 ? { 'WWW-Authenticate': 'Bearer error="invalid_token"' } : undefined,
    );
  }
  const sender = (await deps.senders()).get(token.clientId);
  if (!sender) {
    return fail(403, 'unregistered_sender', `client ${token.clientId} has no sender registration`);
  }
  if (deps.disabled.includes(sender.clientId)) {
    return fail(403, 'sender_disabled', 'this site has turned off notices from this sender');
  }
  const store = await deps.store();
  const { count, retryAfter } = await store.countCall(sender.clientId);
  if (count > deps.rateLimit) {
    return fail(429, 'rate_limited', `at most ${deps.rateLimit} calls a minute`, {
      'Retry-After': String(retryAfter),
    });
  }
  return { sender, store };
}

async function recipients(
  addressed: SendRequest['recipients'],
  sender: Sender,
  deps: SendDeps,
): Promise<Map<string, string | null> | Reply> {
  const tooMany = fail(
    422,
    'too_many_recipients',
    `a notice may reach at most ${deps.maxDeliveries} people`,
  );
  const found = new Map<string, string | null>(addressed.users.map((name) => [name, null]));
  if (found.size > deps.maxDeliveries) return tooMany;
  for (const role of new Set(addressed.roles)) {
    let members;
    try {
      members = await deps.roles.members(sender.clientId, role, deps.maxDeliveries);
    } catch (err) {
      console.error('[notifications] role lookup failed', String(err));
      return fail(503, 'role_lookup_unavailable', 'role members could not be looked up; try again');
    }
    if (!members) return fail(422, 'unknown_role', `client ${sender.clientId} has no role ${role}`);
    if (members.truncated) return tooMany;
    for (const name of members.usernames) if (!found.has(name)) found.set(name, role);
    if (found.size > deps.maxDeliveries) return tooMany;
  }
  return found;
}

function unavailable(err: unknown): Reply {
  console.error('[notifications] request failed', String(err));
  return fail(503, 'unavailable', 'notices are unavailable; try again');
}

export async function sendNotice(
  authorization: string | null,
  readBody: () => Promise<string>,
  deps: SendDeps,
): Promise<Reply> {
  try {
    const auth = await authorize(authorization, deps);
    if ('status' in auth) return auth;
    const { sender, store } = auth;
    const text = await readBody();
    if (Buffer.byteLength(text) > BODY_MAX_BYTES) {
      return fail(413, 'too_large', `the body may be at most ${BODY_MAX_BYTES} bytes`);
    }
    let json: unknown;
    try {
      json = JSON.parse(text);
    } catch {
      return fail(400, 'invalid_json', 'the body must be JSON');
    }
    const parsed = sendSchema.safeParse(json);
    if (!parsed.success) {
      const reasons = parsed.error.issues.map(
        (issue) => `${issue.path.join('.') || 'body'}: ${issue.message}`,
      );
      return fail(400, 'invalid_request', reasons.join('; '));
    }
    const request = parsed.data;
    const checked = checkRequest(request, sender, deps.now?.() ?? new Date());
    if (!checked.ok) return fail(422, checked.error, checked.message);
    if (request.idempotencyKey) {
      const earlier = await store.replay(sender.clientId, request.idempotencyKey);
      if (earlier)
        return { status: 201, body: earlier, headers: { 'Idempotent-Replayed': 'true' } };
    }
    const found = await recipients(request.recipients, sender, deps);
    if (!(found instanceof Map)) return found;
    const { result, replayed } = await store.send(
      {
        sender: sender.clientId,
        senderName: sender.displayName,
        category: request.category,
        title: request.title,
        detail: request.detail,
        linkSubdomain: sender.link.subdomain,
        linkPath: checked.link,
        collapseKey: request.collapseKey,
        expiresAt: checked.expiresAt,
        recipients: found,
      },
      request.idempotencyKey,
    );
    return {
      status: 201,
      body: result,
      headers: replayed ? { 'Idempotent-Replayed': 'true' } : undefined,
    };
  } catch (err) {
    return unavailable(err);
  }
}

export async function withdrawNotice(
  authorization: string | null,
  collapseKey: string | null,
  deps: SendDeps,
): Promise<Reply> {
  try {
    const auth = await authorize(authorization, deps);
    if ('status' in auth) return auth;
    const key = keySchema.safeParse(collapseKey);
    if (!key.success) {
      return fail(400, 'invalid_request', 'name the notice with ?collapseKey=<key>');
    }
    return { status: 200, body: await auth.store.withdraw(auth.sender.clientId, key.data) };
  } catch (err) {
    return unavailable(err);
  }
}
