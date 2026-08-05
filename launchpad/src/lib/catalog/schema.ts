import { parseDocument } from 'yaml';
import { z } from 'zod';
import { DEFAULT_GROUP_ICON, DEFAULT_ICON, ICON_NAMES } from './icons';
import { DEFAULT_TONE, TONE_NAMES } from './tones';
import type { Catalog, Chip, Diagnostic, Group, GroupLayout } from './types';

export const CATALOG_API_VERSION = 'scout.washu.edu/v1alpha1';

export const TITLE_MAX = 60;
export const DESCRIPTION_MAX = 200;
export const ICON_DATA_MAX_CHARS = 16 * 1024;

// DNS-label shape, shared by ids, group refs, and link subdomains.
const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const ICON_DATA_RE = /^data:image\/(?:png|jpeg|svg\+xml);base64,[A-Za-z0-9+/]+={0,2}$/;

// The structural shape shared by ZodError and the error object zod v4 hands
// to .catch() callbacks.
interface IssueBearer {
  issues: Array<{ message: string }>;
}

type FieldDiag = (field: string, error: IssueBearer) => void;

function firstMessage(error: IssueBearer): string {
  return error.issues[0]?.message ?? 'invalid value';
}

// ---------------------------------------------------------------------------
// Base field schemas: the authoring contract, strict, with no fallbacks.
// Both the lenient parsing schemas (below) and the published JSON Schema
// derive from these, so the law and the parser cannot disagree.
// ---------------------------------------------------------------------------

const idSchema = z.string().regex(ID_RE, 'must be a lowercase dns-label-style slug');
const titleSchema = z.string().trim().min(1, 'title is required and must be a non-empty string');
const audienceSchema = z.enum(['user', 'admin']);
const httpUrlSchema = z.url({ protocol: /^https?$/, error: 'must be an absolute http(s) URL' });

const linkSchema = z
  .object({
    subdomain: idSchema.optional(),
    path: z.string().startsWith('/', 'path must start with /').optional(),
    url: httpUrlSchema.optional(),
  })
  .refine(
    (link) => (link.url ? !link.subdomain && !link.path : Boolean(link.subdomain || link.path)),
    'link must be exactly one destination: subdomain (with optional path suffix), path, or url',
  );

const chipBase = {
  id: idSchema,
  title: titleSchema,
  description: z.string().trim(),
  icon: z.enum(ICON_NAMES as [string, ...string[]], { error: 'unknown icon name' }),
  iconData: z
    .string()
    .regex(ICON_DATA_RE, 'iconData must be a base64 png/jpeg/svg+xml data URI')
    .max(ICON_DATA_MAX_CHARS, `iconData must be at most ${ICON_DATA_MAX_CHARS} characters`),
  tone: z.enum(TONE_NAMES),
  link: linkSchema,
  newTab: z.boolean(),
  group: idSchema,
  weight: z.number(),
  audience: audienceSchema,
  enabled: z.boolean(),
};

const groupBase = {
  id: idSchema,
  title: titleSchema,
  description: z.string().trim(),
  icon: z.enum(ICON_NAMES as [string, ...string[]], { error: 'unknown icon name' }),
  weight: z.number(),
  layout: z.enum(['cards', 'rows', 'tiles']),
  maxColumns: z.number().int().min(1).max(4),
  width: z.enum(['full', 'half']),
  audience: audienceSchema,
  footerLink: z.object({
    text: z.string().trim().min(1).max(TITLE_MAX),
    url: z
      .string()
      .refine(
        (v) => v.startsWith('/') || httpUrlSchema.safeParse(v).success,
        'must be an http(s) URL or a path',
      ),
  }),
};

// ---------------------------------------------------------------------------
// Lenient parsing schemas: presentation fields degrade per-field — omitted →
// default silently, invalid → default with a diagnostic. Identity and
// destination fields have no fallback; their failure rejects the whole
// chip/group (the graded budget of ADR 0034).
// ---------------------------------------------------------------------------

// Wrap a base field schema so that an omitted value defaults silently and an
// invalid one falls back with a diagnostic. The `as never` satisfies zod's
// NoUndefined parameter types; none of our fallbacks are undefined.
function caughtWith(diag: FieldDiag) {
  return <T extends z.ZodType>(field: string, base: T, fallback: z.output<T>) => {
    const value = fallback as never;
    return base.default(value).catch((ctx) => {
      diag(field, ctx.error);
      return value;
    });
  };
}

function makeChipSchema(diag: FieldDiag) {
  const caught = caughtWith(diag);
  return z.object({
    id: chipBase.id,
    title: chipBase.title,
    description: caught('description', chipBase.description, ''),
    icon: caught('icon', chipBase.icon, DEFAULT_ICON),
    iconData: chipBase.iconData.optional().catch((ctx) => {
      diag('iconData', ctx.error);
      return undefined;
    }),
    tone: caught('tone', chipBase.tone, DEFAULT_TONE),
    link: chipBase.link,
    newTab: caught('newTab', chipBase.newTab, true),
    group: caught('group', chipBase.group, 'more'),
    weight: caught('weight', chipBase.weight, 100),
    audience: caught('audience', chipBase.audience, 'user'),
    enabled: caught('enabled', chipBase.enabled, true),
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
  const caught = caughtWith(diag);
  return z.object({
    id: groupBase.id,
    title: groupBase.title,
    description: caught('description', groupBase.description, ''),
    icon: caught('icon', groupBase.icon, DEFAULT_GROUP_ICON),
    weight: caught('weight', groupBase.weight, 100),
    layout: caught('layout', groupBase.layout, 'cards'),
    maxColumns: groupBase.maxColumns.optional().catch((ctx) => {
      diag('maxColumns', ctx.error);
      return undefined;
    }),
    width: caught('width', groupBase.width, 'full'),
    audience: caught('audience', groupBase.audience, 'user'),
    footerLink: groupBase.footerLink.optional().catch((ctx) => {
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

// ---------------------------------------------------------------------------
// Publication: the authoring-side JSON Schema shipped with the docs. Built
// from the strict base fields (not the lenient wrappers — a .catch() schema
// accepts anything on input, which would publish no law at all). Defaults are
// annotated; a drift test compares the committed file against this output.
// ---------------------------------------------------------------------------

export function publicationSchema() {
  return z
    .object({
      apiVersion: z
        .literal(CATALOG_API_VERSION)
        .describe('Catalog schema version. Documents with any other value are skipped.'),
      chips: z
        .array(
          z.object({
            id: chipBase.id,
            title: chipBase.title.describe(`Chip heading, at most ${TITLE_MAX} characters`),
            description: chipBase.description
              .max(DESCRIPTION_MAX)
              .default('')
              .describe('One line about the destination'),
            icon: chipBase.icon.default(DEFAULT_ICON),
            iconData: chipBase.iconData
              .optional()
              .describe('Embedded image data URI; wins over icon'),
            tone: chipBase.tone.default(DEFAULT_TONE),
            link: chipBase.link.describe(
              'Exactly one destination: subdomain (with optional path suffix), path, or url',
            ),
            newTab: chipBase.newTab.default(true),
            group: chipBase.group.default('more'),
            weight: chipBase.weight.default(100).describe('Lower renders first'),
            audience: chipBase.audience.default('user'),
            enabled: chipBase.enabled.default(true),
          }),
        )
        .default([]),
      groups: z
        .array(
          z.object({
            id: groupBase.id,
            title: groupBase.title.describe(`Section heading, at most ${TITLE_MAX} characters`),
            description: groupBase.description.max(DESCRIPTION_MAX).default(''),
            icon: groupBase.icon.default(DEFAULT_GROUP_ICON),
            weight: groupBase.weight.default(100).describe('Section order on the page'),
            layout: groupBase.layout.default('cards'),
            maxColumns: groupBase.maxColumns
              .optional()
              .describe('Defaults by layout: cards 3, tiles 2, rows 1'),
            width: groupBase.width.default('full'),
            audience: groupBase.audience.default('user'),
            footerLink: groupBase.footerLink.optional(),
          }),
        )
        .default([])
        .describe('Define a group only when introducing a new section'),
    })
    .describe('Scout launchpad catalog document (ADR 0034)');
}

export function catalogJsonSchema(): unknown {
  return z.toJSONSchema(publicationSchema(), { io: 'input', target: 'draft-2020-12' });
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

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
// chart-templated core document both land here). Never throws: everything
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
