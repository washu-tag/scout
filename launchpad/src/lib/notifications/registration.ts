import { parseAllDocuments } from 'yaml';
import { z } from 'zod';
import type { Diagnostic } from '@/lib/catalog/types';

// A sender's registration (ADR 0043): one document per sender, shipped in a
// ConfigMap labelled launchpad.scout.xnat.org/notifications: "true". A
// registration either validates whole or is skipped; unknown fields are ignored
// with a diagnostic, as in catalog documents.
export const SENDER_API_VERSION = 'launchpad.scout.xnat.org/v1alpha1';
export const SENDER_KIND = 'NotificationSender';
const SENDER_NAME_MAX = 60;

export interface Sender {
  clientId: string;
  displayName: string;
  link: { subdomain: string; pathPrefixes: string[] };
  categories: string[];
  source: string;
}

const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
// The fragment reconciler's client id pattern (ADR 0037).
const CLIENT_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$/;
const BASE = 'https://launchpad.invalid';

// A rooted path already in the form a URL parser gives it: one leading slash,
// no dot segments, encoded where encoding is needed, no query or fragment.
export function isNormalPath(value: string): boolean {
  if (!/^\/(?![/\\])/.test(value) || /%2f|%5c/i.test(value)) return false;
  try {
    const url = new URL(value, BASE);
    return url.origin === BASE && url.pathname === value;
  } catch {
    return false;
  }
}

const idSchema = z.string().regex(ID_RE, 'must be a lowercase dns-label-style slug');

const senderSchema = z.object({
  displayName: z.string().trim().min(1).max(SENDER_NAME_MAX),
  clientId: z.string().regex(CLIENT_ID_RE, 'must be a Keycloak client id'),
  link: z.object({
    subdomain: idSchema,
    pathPrefixes: z
      .array(z.string().refine(isNormalPath, 'must be a normalized rooted path'))
      .min(1),
  }),
  categories: z.array(idSchema).min(1),
});

const KNOWN_KEYS = ['apiVersion', 'kind', ...Object.keys(senderSchema.shape)];
const LINK_KEYS = Object.keys(senderSchema.shape.link.shape);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function unknownKeys(value: unknown, known: string[]): string[] {
  return isRecord(value) ? Object.keys(value).filter((key) => !known.includes(key)) : [];
}

export function validateSender(
  input: unknown,
  source: string,
): { sender?: Sender; diagnostics: Diagnostic[] } {
  if (!isRecord(input)) {
    return { diagnostics: [{ source, message: 'document is not a mapping; skipped' }] };
  }
  if (input.apiVersion !== SENDER_API_VERSION || input.kind !== SENDER_KIND) {
    return {
      diagnostics: [
        {
          source,
          message: `unknown document type "${input.apiVersion} ${input.kind}" (expected apiVersion ${SENDER_API_VERSION}, kind ${SENDER_KIND}); skipped`,
        },
      ],
    };
  }
  const subject = typeof input.clientId === 'string' ? input.clientId : undefined;
  const result = senderSchema.safeParse(input);
  if (!result.success) {
    const reasons = result.error.issues
      .map((issue) => `${issue.path.join('.') || 'sender'}: ${issue.message}`)
      .join('; ');
    return { diagnostics: [{ source, subject, message: `sender skipped: ${reasons}` }] };
  }
  const diagnostics = [
    ...unknownKeys(input, KNOWN_KEYS),
    ...unknownKeys(input.link, LINK_KEYS).map((key) => `link.${key}`),
  ].map((key) => ({ source, subject, message: `unknown field "${key}" ignored` }));
  const { displayName, clientId, link, categories } = result.data;
  return {
    sender: {
      displayName,
      clientId,
      link: { subdomain: link.subdomain, pathPrefixes: [...new Set(link.pathPrefixes)] },
      categories: [...new Set(categories)],
      source,
    },
    diagnostics,
  };
}

export function parseSenderText(
  text: string,
  source: string,
): { senders: Sender[]; diagnostics: Diagnostic[] } {
  const documents = parseAllDocuments(text, { prettyErrors: true });
  const senders: Sender[] = [];
  const diagnostics: Diagnostic[] = [];
  documents.forEach((doc, index) => {
    const docSource = documents.length > 1 ? `${source} (document ${index + 1})` : source;
    if (doc.errors.length > 0) {
      diagnostics.push(
        ...doc.errors.map((err) => ({
          source: docSource,
          message: `YAML parse error: ${err.message.split('\n')[0]}`,
        })),
      );
      return;
    }
    const value = doc.toJS();
    if (value === null || value === undefined) return;
    const parsed = validateSender(value, docSource);
    if (parsed.sender) senders.push(parsed.sender);
    diagnostics.push(...parsed.diagnostics);
  });
  return { senders, diagnostics };
}

// Two registrations naming one client are both refused: neither may decide
// the other's name or link host.
export function indexSenders(senders: Sender[]): {
  byClient: Map<string, Sender>;
  diagnostics: Diagnostic[];
} {
  const claims = new Map<string, Sender[]>();
  for (const sender of senders) {
    claims.set(sender.clientId, [...(claims.get(sender.clientId) ?? []), sender]);
  }
  const byClient = new Map<string, Sender>();
  const diagnostics: Diagnostic[] = [];
  for (const [clientId, claimants] of claims) {
    if (claimants.length === 1) {
      byClient.set(clientId, claimants[0]);
      continue;
    }
    const sources = claimants.map((sender) => sender.source).join(', ');
    for (const sender of claimants) {
      diagnostics.push({
        source: sender.source,
        subject: clientId,
        message: `client "${clientId}" is registered more than once (${sources}); every registration of it skipped`,
      });
    }
  }
  return { byClient, diagnostics };
}
