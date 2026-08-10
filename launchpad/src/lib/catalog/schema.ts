import { parseAllDocuments } from 'yaml';
import { z } from 'zod';
import { DEFAULT_GROUP_ICON, DEFAULT_ICON, ICON_NAMES } from './icons';
import { DEFAULT_TONE, TONE_NAMES } from './tones';
import type { Catalog, Chip, Diagnostic, Group, GroupLayout } from './types';

export const CATALOG_API_VERSION = 'scout.washu.edu/v1alpha1';

export const TITLE_MAX = 60;
export const DESCRIPTION_MAX = 200;
export const ICON_DATA_MAX_CHARS = 16 * 1024;

// Where a chip lands when it names no group. The core catalog defines the
// section; the schema supplies the reference.
const DEFAULT_GROUP = 'more';

// DNS-label shape, shared by ids, group refs, and link subdomains.
const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const ICON_DATA_RE = /^data:image\/(?:png|jpeg|svg\+xml);base64,[A-Za-z0-9+/]+={0,2}$/;
// Exactly one leading slash: '//host' (and '/\host', which browsers treat
// the same) is protocol-relative — an off-site destination disguised as a
// same-origin path.
const ROOTED_PATH_RE = /^\/(?![/\\])/;

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
// derive from these, the known-key lists are derived from the same tables,
// and the fallback values are single-sourced — so the law, the parser, and
// the documentation cannot disagree.
// ---------------------------------------------------------------------------

const idSchema = z.string().regex(ID_RE, 'must be a lowercase dns-label-style slug');
const titleSchema = z.string().trim().min(1, 'title is required and must be a non-empty string');
const audienceSchema = z.enum(['user', 'admin']);
const httpUrlSchema = z.url({ protocol: /^https?$/, error: 'must be an absolute http(s) URL' });
const rootedPathSchema = z
  .string()
  .regex(ROOTED_PATH_RE, 'must be a rooted path with a single leading /');

const linkShape = {
  subdomain: idSchema.optional(),
  path: rootedPathSchema.optional(),
  url: httpUrlSchema.optional(),
};

const linkSchema = z
  .object(linkShape)
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
        (v) => ROOTED_PATH_RE.test(v) || httpUrlSchema.safeParse(v).success,
        'must be an http(s) URL or a rooted path',
      ),
  }),
};

// Known-key lists for the forward-compatibility warning, derived from the
// field tables so a new field can never be forgotten here.
const CHIP_KEYS = Object.keys(chipBase);
const GROUP_KEYS = Object.keys(groupBase);
const LINK_KEYS = Object.keys(linkShape);

// Fallback values, shared by the lenient parser and the published JSON
// Schema so the documented default and the applied default are the same
// value.
const CHIP_FALLBACKS = {
  description: '',
  icon: DEFAULT_ICON,
  tone: DEFAULT_TONE,
  newTab: true,
  group: DEFAULT_GROUP,
  weight: 100,
  audience: 'user',
  enabled: true,
} as const;

const GROUP_FALLBACKS = {
  description: '',
  icon: DEFAULT_GROUP_ICON,
  weight: 100,
  layout: 'cards',
  width: 'full',
  audience: 'user',
} as const;

// ---------------------------------------------------------------------------
// Lenient parsing schemas: presentation fields degrade per-field — omitted →
// default silently, invalid → default with a diagnostic. Identity and
// destination fields have no fallback, and the visibility fields (audience,
// enabled) fail closed: their failure rejects the whole chip/group rather
// than defaulting it into view (the graded budget of ADR 0034).
// ---------------------------------------------------------------------------

// Where field-level diagnostics land during a parse. The schemas are built
// once at module load; validateDocument swaps a collector in around each
// synchronous safeParse instead of rebuilding the schema graph per element.
let fieldDiagSink: FieldDiag = () => {};

function parseWithFieldDiags<T extends z.ZodType>(schema: T, raw: unknown, collect: FieldDiag) {
  fieldDiagSink = collect;
  try {
    return schema.safeParse(raw);
  } finally {
    fieldDiagSink = () => {};
  }
}

// Wrap a base field schema so that an omitted value defaults silently and an
// invalid one falls back with a diagnostic. The `as never` satisfies zod's
// NoUndefined parameter types; the fallback itself is type-checked against
// the base schema's output at the call site.
function caught<T extends z.ZodType>(field: string, base: T, fallback: z.output<T>) {
  const value = fallback as never;
  return base.default(value).catch((ctx) => {
    fieldDiagSink(field, ctx.error);
    return value;
  });
}

// Optional field: omitted → undefined silently, invalid → undefined with a
// diagnostic.
function caughtOptional<T extends z.ZodType>(field: string, base: T) {
  return base.optional().catch((ctx) => {
    fieldDiagSink(field, ctx.error);
    return undefined;
  });
}

const chipSchema = z.object({
  id: chipBase.id,
  title: chipBase.title,
  description: caught('description', chipBase.description, CHIP_FALLBACKS.description),
  icon: caught('icon', chipBase.icon, CHIP_FALLBACKS.icon),
  iconData: caughtOptional('iconData', chipBase.iconData),
  tone: caught('tone', chipBase.tone, CHIP_FALLBACKS.tone),
  link: chipBase.link,
  newTab: caught('newTab', chipBase.newTab, CHIP_FALLBACKS.newTab),
  group: caught('group', chipBase.group, CHIP_FALLBACKS.group),
  weight: caught('weight', chipBase.weight, CHIP_FALLBACKS.weight),
  // Visibility fails closed: no .catch() here, so an invalid value rejects
  // the chip instead of widening who sees it.
  audience: chipBase.audience.default(CHIP_FALLBACKS.audience),
  enabled: chipBase.enabled.default(CHIP_FALLBACKS.enabled),
});

const groupSchema = z.object({
  id: groupBase.id,
  title: groupBase.title,
  description: caught('description', groupBase.description, GROUP_FALLBACKS.description),
  icon: caught('icon', groupBase.icon, GROUP_FALLBACKS.icon),
  weight: caught('weight', groupBase.weight, GROUP_FALLBACKS.weight),
  layout: caught('layout', groupBase.layout, GROUP_FALLBACKS.layout),
  maxColumns: caughtOptional('maxColumns', groupBase.maxColumns),
  width: caught('width', groupBase.width, GROUP_FALLBACKS.width),
  // Fail closed, as for chips: a definition that cannot state its audience
  // is dropped (member chips still render via their own audience).
  audience: groupBase.audience.default(GROUP_FALLBACKS.audience),
  footerLink: caughtOptional('footerLink', groupBase.footerLink),
});

// A present-but-empty key (`chips:` with nothing under it) parses from YAML
// as null; treat it as an empty list rather than rejecting the document.
const documentList = z.preprocess((value) => value ?? [], z.array(z.unknown()));

const envelopeSchema = z.object({
  apiVersion: z.string({ error: 'apiVersion is required' }),
  chips: documentList,
  groups: documentList,
});

const ENVELOPE_KEYS = Object.keys(envelopeSchema.shape);

// ---------------------------------------------------------------------------
// Publication: the authoring-side JSON Schema shipped with the docs. Built
// from the strict base fields (not the lenient wrappers — a .catch() schema
// accepts anything on input, which would publish no law at all). Defaults
// are annotated from the shared fallbacks; a drift test compares the
// committed file against this output.
// ---------------------------------------------------------------------------

const publicationSchemaValue = z
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
            .default(CHIP_FALLBACKS.description)
            .describe('One line about the destination'),
          icon: chipBase.icon.default(CHIP_FALLBACKS.icon),
          iconData: chipBase.iconData
            .optional()
            .describe('Embedded image data URI; wins over icon'),
          tone: chipBase.tone.default(CHIP_FALLBACKS.tone),
          link: chipBase.link.describe(
            'Exactly one destination: subdomain (with optional path suffix), path, or url',
          ),
          newTab: chipBase.newTab.default(CHIP_FALLBACKS.newTab),
          group: chipBase.group.default(CHIP_FALLBACKS.group),
          weight: chipBase.weight.default(CHIP_FALLBACKS.weight).describe('Lower renders first'),
          audience: chipBase.audience.default(CHIP_FALLBACKS.audience),
          enabled: chipBase.enabled.default(CHIP_FALLBACKS.enabled),
        }),
      )
      .default([]),
    groups: z
      .array(
        z.object({
          id: groupBase.id,
          title: groupBase.title.describe(`Section heading, at most ${TITLE_MAX} characters`),
          description: groupBase.description
            .max(DESCRIPTION_MAX)
            .default(GROUP_FALLBACKS.description),
          icon: groupBase.icon.default(GROUP_FALLBACKS.icon),
          weight: groupBase.weight
            .default(GROUP_FALLBACKS.weight)
            .describe('Section order on the page'),
          layout: groupBase.layout.default(GROUP_FALLBACKS.layout),
          maxColumns: groupBase.maxColumns
            .optional()
            .describe('Defaults by layout: cards 3, tiles 2, rows 1'),
          width: groupBase.width.default(GROUP_FALLBACKS.width),
          audience: groupBase.audience.default(GROUP_FALLBACKS.audience),
          footerLink: groupBase.footerLink.optional(),
        }),
      )
      .default([])
      .describe('Define a group only when introducing a new section'),
  })
  .describe('Scout launchpad catalog document (ADR 0034)');

export function publicationSchema() {
  return publicationSchemaValue;
}

export function catalogJsonSchema(): unknown {
  return z.toJSONSchema(publicationSchemaValue, { io: 'input', target: 'draft-2020-12' });
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
    const result = parseWithFieldDiags(chipSchema, raw, (field, error) =>
      fieldDiags.push({
        source,
        subject,
        message: `invalid ${field} (${firstMessage(error)}); default used`,
      }),
    );
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
      warnUnknownKeys(raw.link, LINK_KEYS, diagnostics, source, subject);
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
    const result = parseWithFieldDiags(groupSchema, raw, (field, error) =>
      fieldDiags.push({
        source,
        subject,
        message: `invalid ${field} (${firstMessage(error)}); default used`,
      }),
    );
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

// Parse the catalog documents in one YAML text — a ConfigMap data key may
// hold several, separated by `---`. Parse errors cost only the document that
// carries them; the eemeli parser collects every error with line/column
// positions so the diagnostic can say where.
export function parseCatalogText(text: string, source: string, sourceRank = 0): Catalog {
  const documents = parseAllDocuments(text, { prettyErrors: true });
  const catalog: Catalog = { chips: [], groups: [], diagnostics: [] };
  documents.forEach((doc, index) => {
    const docSource = documents.length > 1 ? `${source} (document ${index + 1})` : source;
    if (doc.errors.length > 0) {
      catalog.diagnostics.push(
        ...doc.errors.map((err) => ({
          source: docSource,
          message: `YAML parse error: ${err.message.split('\n')[0]}${
            err.linePos ? ` (line ${err.linePos[0].line}, col ${err.linePos[0].col})` : ''
          }`,
        })),
      );
      return;
    }
    const parsed = validateDocument(doc.toJS(), docSource, sourceRank);
    catalog.chips.push(...parsed.chips);
    catalog.groups.push(...parsed.groups);
    catalog.diagnostics.push(...parsed.diagnostics);
  });
  return catalog;
}
