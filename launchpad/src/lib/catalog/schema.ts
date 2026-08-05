import { parseDocument } from 'yaml';
import { z } from 'zod';
import { DEFAULT_GROUP_ICON, DEFAULT_ICON, isIconName } from './icons';
import { DEFAULT_TONE, TONE_NAMES } from './tones';
import type { Audience, Catalog, Chip, Diagnostic, Group, GroupLayout, GroupWidth } from './types';

export const CATALOG_API_VERSION = 'scout.washu.edu/v1alpha1';

export const TITLE_MAX = 60;
export const DESCRIPTION_MAX = 200;
export const ICON_DATA_MAX_CHARS = 16 * 1024;

// DNS-label shape, shared by ids, group refs, and link subdomains.
const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const ICON_DATA_RE = /^data:image\/(?:png|jpeg|svg\+xml);base64,[A-Za-z0-9+/]+={0,2}$/;

function isHttpUrl(value: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  return parsed.protocol === 'http:' || parsed.protocol === 'https:';
}

// The structural shape shared by ZodError and the error object zod v4 hands
// to .catch() callbacks.
interface IssueBearer {
  issues: Array<{ message: string }>;
}

type FieldDiag = (field: string, error: IssueBearer) => void;

function firstMessage(error: IssueBearer): string {
  return error.issues[0]?.message ?? 'invalid value';
}

// Presentation fields degrade per-field: omitted → default silently, invalid →
// default with a diagnostic. Identity and destination fields have no fallback;
// their failure rejects the whole chip/group (the graded budget of ADR 0034).
const audienceSchema = z.enum(['user', 'admin']);

function makeChipSchema(diag: FieldDiag) {
  return z.object({
    id: z.string().regex(ID_RE, 'id must be a lowercase dns-label-style slug'),
    title: z.string().trim().min(1, 'title is required and must be a non-empty string'),
    description: z
      .string()
      .trim()
      .default('')
      .catch((ctx) => {
        diag('description', ctx.error);
        return '';
      }),
    icon: z
      .string()
      .refine(isIconName, 'unknown icon name')
      .default(DEFAULT_ICON)
      .catch((ctx) => {
        diag('icon', ctx.error);
        return DEFAULT_ICON;
      }),
    iconData: z
      .string()
      .regex(ICON_DATA_RE, 'iconData must be a base64 png/jpeg/svg+xml data URI')
      .max(ICON_DATA_MAX_CHARS, `iconData must be at most ${ICON_DATA_MAX_CHARS} characters`)
      .optional()
      .catch((ctx) => {
        diag('iconData', ctx.error);
        return undefined;
      }),
    tone: z
      .enum(TONE_NAMES)
      .default(DEFAULT_TONE)
      .catch((ctx) => {
        diag('tone', ctx.error);
        return DEFAULT_TONE;
      }),
    link: z
      .object({
        subdomain: z.string().regex(ID_RE, 'subdomain must be a dns label').optional(),
        path: z.string().startsWith('/', 'path must start with /').optional(),
        url: z.string().refine(isHttpUrl, 'url must be an absolute http(s) URL').optional(),
      })
      .refine(
        (link) => (link.url ? !link.subdomain && !link.path : Boolean(link.subdomain || link.path)),
        'link must be exactly one destination: subdomain (with optional path suffix), path, or url',
      ),
    newTab: z
      .boolean()
      .default(true)
      .catch((ctx) => {
        diag('newTab', ctx.error);
        return true;
      }),
    group: z
      .string()
      .regex(ID_RE, 'group must reference a dns-label-style group id')
      .default('more')
      .catch((ctx) => {
        diag('group', ctx.error);
        return 'more';
      }),
    weight: z
      .number()
      .default(100)
      .catch((ctx) => {
        diag('weight', ctx.error);
        return 100;
      }),
    audience: audienceSchema.default('user').catch((ctx) => {
      diag('audience', ctx.error);
      return 'user' as Audience;
    }),
    enabled: z
      .boolean()
      .default(true)
      .catch((ctx) => {
        diag('enabled', ctx.error);
        return true;
      }),
  });
}

const CHIP_KEYS = [
  'id',
  'title',
  'description',
  'icon',
  'iconData',
  'tone',
  'link',
  'newTab',
  'group',
  'weight',
  'audience',
  'enabled',
];

function makeGroupSchema(diag: FieldDiag) {
  return z.object({
    id: z.string().regex(ID_RE, 'id must be a lowercase dns-label-style slug'),
    title: z.string().trim().min(1, 'title is required and must be a non-empty string'),
    description: z
      .string()
      .trim()
      .default('')
      .catch((ctx) => {
        diag('description', ctx.error);
        return '';
      }),
    icon: z
      .string()
      .refine(isIconName, 'unknown icon name')
      .default(DEFAULT_GROUP_ICON)
      .catch((ctx) => {
        diag('icon', ctx.error);
        return DEFAULT_GROUP_ICON;
      }),
    weight: z
      .number()
      .default(100)
      .catch((ctx) => {
        diag('weight', ctx.error);
        return 100;
      }),
    layout: z
      .enum(['cards', 'rows', 'tiles'])
      .default('cards')
      .catch((ctx) => {
        diag('layout', ctx.error);
        return 'cards' as GroupLayout;
      }),
    maxColumns: z
      .number()
      .int()
      .min(1)
      .max(4)
      .optional()
      .catch((ctx) => {
        diag('maxColumns', ctx.error);
        return undefined;
      }),
    width: z
      .enum(['full', 'half'])
      .default('full')
      .catch((ctx) => {
        diag('width', ctx.error);
        return 'full' as GroupWidth;
      }),
    audience: audienceSchema.default('user').catch((ctx) => {
      diag('audience', ctx.error);
      return 'user' as Audience;
    }),
    footerLink: z
      .object({
        text: z.string().trim().min(1).max(TITLE_MAX),
        url: z
          .string()
          .refine((v) => isHttpUrl(v) || v.startsWith('/'), 'must be an http(s) URL or a path'),
      })
      .optional()
      .catch((ctx) => {
        diag('footerLink', ctx.error);
        return undefined;
      }),
  });
}

const GROUP_KEYS = [
  'id',
  'title',
  'description',
  'icon',
  'weight',
  'layout',
  'maxColumns',
  'width',
  'audience',
  'footerLink',
];

const envelopeSchema = z.object({
  apiVersion: z.string({ error: 'apiVersion is required' }),
  chips: z.array(z.unknown()).default([]),
  groups: z.array(z.unknown()).default([]),
});

const ENVELOPE_KEYS = ['apiVersion', 'chips', 'groups'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function warnUnknownKeys(
  value: unknown,
  known: string[],
  diagnostics: Diagnostic[],
  source: string,
  subject: string,
): void {
  if (!isRecord(value)) return;
  for (const key of Object.keys(value)) {
    if (!known.includes(key)) {
      diagnostics.push({
        source,
        subject,
        message: `unknown field "${key}" ignored (forward compatibility)`,
      });
    }
  }
}

function truncate(
  value: string,
  max: number,
  field: string,
  diagnostics: Diagnostic[],
  source: string,
  subject: string,
): string {
  if (value.length <= max) return value;
  diagnostics.push({
    source,
    subject,
    message: `${field} longer than ${max} characters; truncated`,
  });
  return value.slice(0, max);
}

// Validate one already-parsed catalog document (the YAML text path and the
// chart-templated builtin document both land here). Never throws: everything
// that goes wrong becomes a diagnostic, and everything valid comes back.
export function validateDocument(input: unknown, source: string, sourceRank = 0): Catalog {
  const diagnostics: Diagnostic[] = [];
  const chips: Chip[] = [];
  const groups: Group[] = [];

  if (!isRecord(input)) {
    diagnostics.push({ source, message: 'document is not a mapping; skipped' });
    return { chips, groups, diagnostics };
  }

  const envelope = envelopeSchema.safeParse(input);
  if (!envelope.success) {
    diagnostics.push({
      source,
      message: `not a catalog document: ${firstMessage(envelope.error)}`,
    });
    return { chips, groups, diagnostics };
  }
  if (envelope.data.apiVersion !== CATALOG_API_VERSION) {
    diagnostics.push({
      source,
      message: `unknown apiVersion "${envelope.data.apiVersion}" (expected ${CATALOG_API_VERSION}); document skipped`,
    });
    return { chips, groups, diagnostics };
  }
  warnUnknownKeys(input, ENVELOPE_KEYS, diagnostics, source, 'document');

  const seenChipIds = new Set<string>();
  envelope.data.chips.forEach((raw, index) => {
    const fallbackSubject = `chips[${index}]`;
    const subject = isRecord(raw) && typeof raw.id === 'string' ? raw.id : fallbackSubject;
    const fieldDiags: Diagnostic[] = [];
    const schema = makeChipSchema((field, error) =>
      fieldDiags.push({
        source,
        subject,
        message: `invalid ${field} (${firstMessage(error)}); default used`,
      }),
    );
    const result = schema.safeParse(raw);
    if (!result.success) {
      diagnostics.push({
        source,
        subject,
        message: `chip skipped: ${result.error.issues
          .map((issue) => `${issue.path.join('.') || 'chip'}: ${issue.message}`)
          .join('; ')}`,
      });
      return;
    }
    diagnostics.push(...fieldDiags);
    warnUnknownKeys(raw, CHIP_KEYS, diagnostics, source, subject);
    if (isRecord(raw) && isRecord(raw.link)) {
      warnUnknownKeys(raw.link, ['subdomain', 'path', 'url'], diagnostics, source, subject);
    }
    if (seenChipIds.has(result.data.id)) {
      diagnostics.push({
        source,
        subject,
        message: `duplicate chip id "${result.data.id}" in this document; later chip skipped`,
      });
      return;
    }
    seenChipIds.add(result.data.id);
    chips.push({
      ...result.data,
      title: truncate(result.data.title, TITLE_MAX, 'title', diagnostics, source, subject),
      description: truncate(
        result.data.description,
        DESCRIPTION_MAX,
        'description',
        diagnostics,
        source,
        subject,
      ),
      source,
    });
  });

  const seenGroupIds = new Set<string>();
  envelope.data.groups.forEach((raw, index) => {
    const fallbackSubject = `groups[${index}]`;
    const subject = isRecord(raw) && typeof raw.id === 'string' ? raw.id : fallbackSubject;
    const fieldDiags: Diagnostic[] = [];
    const schema = makeGroupSchema((field, error) =>
      fieldDiags.push({
        source,
        subject,
        message: `invalid ${field} (${firstMessage(error)}); default used`,
      }),
    );
    const result = schema.safeParse(raw);
    if (!result.success) {
      diagnostics.push({
        source,
        subject,
        message: `group skipped: ${result.error.issues
          .map((issue) => `${issue.path.join('.') || 'group'}: ${issue.message}`)
          .join('; ')}`,
      });
      return;
    }
    diagnostics.push(...fieldDiags);
    warnUnknownKeys(raw, GROUP_KEYS, diagnostics, source, subject);
    if (seenGroupIds.has(result.data.id)) {
      diagnostics.push({
        source,
        subject,
        message: `duplicate group id "${result.data.id}" in this document; later group skipped`,
      });
      return;
    }
    seenGroupIds.add(result.data.id);
    const { maxColumns, ...rest } = result.data;
    groups.push({
      ...rest,
      title: truncate(result.data.title, TITLE_MAX, 'title', diagnostics, source, subject),
      description: truncate(
        result.data.description,
        DESCRIPTION_MAX,
        'description',
        diagnostics,
        source,
        subject,
      ),
      maxColumns: maxColumns ?? defaultColumns(result.data.layout),
      source,
      sourceRank,
    });
  });

  return { chips, groups, diagnostics };
}

export function defaultColumns(layout: GroupLayout): number {
  switch (layout) {
    case 'cards':
      return 3;
    case 'tiles':
      return 2;
    case 'rows':
      return 1;
  }
}

// Parse one catalog document from YAML text. Parse errors cost this document
// only; the eemeli parser collects every error with line/column positions so
// the diagnostic can say where.
export function parseCatalogText(text: string, source: string, sourceRank = 0): Catalog {
  const doc = parseDocument(text, { prettyErrors: true });
  if (doc.errors.length > 0) {
    return {
      chips: [],
      groups: [],
      diagnostics: doc.errors.map((err) => ({
        source,
        message: `YAML parse error: ${err.message.split('\n')[0]}${
          err.linePos ? ` (line ${err.linePos[0].line}, col ${err.linePos[0].col})` : ''
        }`,
      })),
    };
  }
  return validateDocument(doc.toJS(), source, sourceRank);
}
