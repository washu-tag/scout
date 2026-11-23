import { z } from 'zod';
import { isNormalPath, type Sender } from './registration';

const TITLE_MAX = 120;
const DETAIL_MAX = 300;
const KEY_MAX = 200;
const ROLES_MAX = 20;
const RETENTION_DAYS = 90;
const LINK_MAX = 2000;
const BASE = 'https://launchpad.invalid';

// Control characters, and the bidirectional overrides that make text read
// differently from what it says.
const UNSAFE_TEXT = /[\p{Cc}\u202a-\u202e\u2066-\u2069]/u;

function plainText(max: number) {
  return z
    .string()
    .trim()
    .min(1)
    .refine((value) => !UNSAFE_TEXT.test(value), 'must be plain text on one line')
    .refine((value) => [...value].length <= max, `must be at most ${max} characters`);
}

// Keycloak lowercases usernames.
const usernameSchema = z
  .string()
  .trim()
  .toLowerCase()
  .regex(/^[\p{L}\p{N}._@+-]{1,255}$/u, 'must be a username');

// The fragment reconciler's role name pattern (ADR 0037). A role is always one
// of the sender's own client's, so the name carries no client.
const roleSchema = z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$/, 'must be a role name');

export const keySchema = z
  .string()
  .regex(new RegExp(`^[\\x21-\\x7e]{1,${KEY_MAX}}$`), `must be 1-${KEY_MAX} printable characters`);

export const sendSchema = z.strictObject({
  recipients: z
    .strictObject({
      users: z.array(usernameSchema).max(10_000).default([]),
      roles: z.array(roleSchema).max(ROLES_MAX).default([]),
    })
    .refine((r) => r.users.length + r.roles.length > 0, 'name at least one user or role'),
  category: z.string(),
  title: plainText(TITLE_MAX),
  detail: plainText(DETAIL_MAX).optional(),
  link: z.string().max(LINK_MAX),
  collapseKey: keySchema.optional(),
  idempotencyKey: keySchema.optional(),
  expiresAt: z.iso.datetime({ offset: true }).optional(),
});

export type SendRequest = z.infer<typeof sendSchema>;

// `prefix` covers `pathname` when they are equal or the path continues below
// it: /requests covers /requests/7 but not /requests-old.
export function underPrefix(pathname: string, prefix: string): boolean {
  if (pathname === prefix) return true;
  return pathname.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`);
}

// The link a sender gave, normalized, when it is a path on the sender's own
// subdomain under one of its registered prefixes; else null.
export function senderLink(link: string, sender: Sender): string | null {
  if (!/^\/(?![/\\])/.test(link) || /[\\\s]/.test(link) || UNSAFE_TEXT.test(link)) return null;
  let url: URL;
  try {
    url = new URL(link, BASE);
  } catch {
    return null;
  }
  if (url.origin !== BASE || !isNormalPath(url.pathname)) return null;
  if (!sender.link.pathPrefixes.some((prefix) => underPrefix(url.pathname, prefix))) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

export type Checked =
  | { ok: true; link: string; expiresAt: Date }
  | { ok: false; error: string; message: string };

// The rules a schema can't state: they depend on the sender's registration.
export function checkRequest(request: SendRequest, sender: Sender, now: Date): Checked {
  if (!sender.categories.includes(request.category)) {
    return {
      ok: false,
      error: 'unknown_category',
      message: `category must be one of: ${sender.categories.join(', ')}`,
    };
  }
  const link = senderLink(request.link, sender);
  if (!link) {
    return {
      ok: false,
      error: 'link_not_allowed',
      message: `link must be a path under ${sender.link.pathPrefixes.join(', ')}`,
    };
  }
  const retention = new Date(now.getTime() + RETENTION_DAYS * 86_400_000);
  const requested = request.expiresAt ? new Date(request.expiresAt) : retention;
  if (requested <= now) {
    return { ok: false, error: 'already_expired', message: 'expiresAt must be in the future' };
  }
  return { ok: true, link, expiresAt: requested < retention ? requested : retention };
}
