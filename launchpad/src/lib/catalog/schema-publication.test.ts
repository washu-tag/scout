import { readFileSync } from 'fs';
import { describe, expect, it } from 'vitest';
import { catalogJsonSchema } from './schema';

// The published JSON Schema is generated from the same zod definitions the
// validator runs (ADR 0034) — this test is the drift guard. If it fails, run
// `npm run schema` and commit the result.
const PUBLISHED = new URL(
  '../../../../docs/source/technical/launchpad-catalog.v1alpha1.schema.json',
  import.meta.url,
);

describe('published JSON Schema', () => {
  it('matches the zod source (run `npm run schema` after schema changes)', () => {
    const published = JSON.parse(readFileSync(PUBLISHED, 'utf-8'));
    expect(published).toEqual(catalogJsonSchema());
  });

  it('publishes the authoring law, not the lenient parse', () => {
    const schema = catalogJsonSchema() as {
      properties: {
        chips: { items: { properties: Record<string, unknown>; required?: string[] } };
      };
    };
    const chip = schema.properties.chips.items;
    // Required core survives; presentation fields carry defaults and enums.
    expect(chip.required).toEqual(expect.arrayContaining(['id', 'title', 'link']));
    expect(chip.properties.tone).toMatchObject({ default: 'indigo' });
    expect(chip.properties.icon).toMatchObject({ default: 'app' });
    expect((chip.properties.icon as { enum: string[] }).enum).toContain('download');
  });
});
