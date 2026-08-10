// Regenerates the published catalog JSON Schema from the zod source of truth
// (ADR 0034). Run with `npm run schema` after changing the catalog schema;
// schema-publication.test.ts fails when the committed file drifts.
import { mkdirSync, writeFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { catalogJsonSchema } from '../src/lib/catalog/schema';

const target = resolve(
  import.meta.dirname,
  '../../docs/source/customize/launchpad-catalog.v1alpha1.schema.json',
);

mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, `${JSON.stringify(catalogJsonSchema(), null, 2)}\n`);
console.log(`wrote ${target}`);
