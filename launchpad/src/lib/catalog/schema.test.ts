import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { CATALOG_API_VERSION, CATALOG_KIND, parseCatalogText, validateDocument } from './schema';

const SOURCE = 'test/apps.yaml';

function doc(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    apiVersion: CATALOG_API_VERSION,
    kind: CATALOG_KIND,
    chips: [],
    groups: [],
    ...overrides,
  };
}

function chip(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { id: 'demo', title: 'Demo', link: { subdomain: 'demo' }, ...overrides };
}

describe('validateDocument', () => {
  it('applies defaults to a minimal chip', () => {
    const result = validateDocument(doc({ chips: [chip()] }), SOURCE);
    assert.deepStrictEqual(result.diagnostics, []);
    assert.strictEqual(result.chips.length, 1);
    const parsed = result.chips[0];
    assert.partialDeepStrictEqual(parsed, {
      id: 'demo',
      title: 'Demo',
      description: '',
      icon: 'app',
      tone: 'indigo',
      newTab: true,
      group: 'more',
      weight: 100,
      audience: 'user',
      enabled: true,
      source: SOURCE,
    });
  });

  it('skips a chip without a title, keeping its siblings', () => {
    const result = validateDocument(
      doc({ chips: [chip({ id: 'bad', title: '' }), chip({ id: 'good' })] }),
      SOURCE,
    );
    assert.deepStrictEqual(
      result.chips.map((c) => c.id),
      ['good'],
    );
    assert.strictEqual(result.diagnostics.length, 1);
    assert.partialDeepStrictEqual(result.diagnostics[0], { subject: 'bad' });
    assert.match(result.diagnostics[0].message, /chip skipped/);
  });

  it('falls back per-field on bad presentation values, with diagnostics', () => {
    const result = validateDocument(
      doc({ chips: [chip({ icon: 'not-an-icon', tone: 'mauve', weight: 'heavy' })] }),
      SOURCE,
    );
    assert.strictEqual(result.chips.length, 1);
    assert.partialDeepStrictEqual(result.chips[0], { icon: 'app', tone: 'indigo', weight: 100 });
    const fields = result.diagnostics.map((d) => d.message);
    assert.ok(
      fields.some((m) => m.includes('invalid icon')),
      `no invalid-icon diagnostic in ${fields.join(' | ')}`,
    );
    assert.ok(
      fields.some((m) => m.includes('invalid tone')),
      `no invalid-tone diagnostic in ${fields.join(' | ')}`,
    );
    assert.ok(
      fields.some((m) => m.includes('invalid weight')),
      `no invalid-weight diagnostic in ${fields.join(' | ')}`,
    );
  });

  it('rejects non-http(s) destinations outright', () => {
    for (const link of [
      { url: 'javascript:alert(1)' },
      { url: 'data:text/html;base64,xx' },
      { url: 'ftp://example.com' },
    ]) {
      const result = validateDocument(doc({ chips: [chip({ link })] }), SOURCE);
      assert.strictEqual(result.chips.length, 0, JSON.stringify(link));
      assert.match(result.diagnostics[0].message, /chip skipped/);
    }
  });

  it('accepts the three destination shapes and rejects mixes', () => {
    const shapes = [
      { subdomain: 'demo' },
      { subdomain: 'demo', path: '/auth/sso' },
      { path: '/admin/users' },
      { url: 'https://example.com/docs' },
    ];
    for (const link of shapes) {
      const result = validateDocument(doc({ chips: [chip({ link })] }), SOURCE);
      assert.strictEqual(result.chips.length, 1, JSON.stringify(link));
    }
    for (const link of [{}, { url: 'https://example.com', subdomain: 'demo' }]) {
      const result = validateDocument(doc({ chips: [chip({ link })] }), SOURCE);
      assert.strictEqual(result.chips.length, 0, JSON.stringify(link));
    }
  });

  it('drops an invalid iconData but keeps the chip', () => {
    const result = validateDocument(
      doc({ chips: [chip({ iconData: 'data:text/html;base64,AAAA' })] }),
      SOURCE,
    );
    assert.strictEqual(result.chips.length, 1);
    assert.strictEqual(result.chips[0].iconData, undefined);
    assert.match(result.diagnostics[0].message, /invalid iconData/);
  });

  it('accepts a valid image data URI', () => {
    const iconData = 'data:image/png;base64,iVBORw0KGgo=';
    const result = validateDocument(doc({ chips: [chip({ iconData })] }), SOURCE);
    assert.strictEqual(result.chips[0].iconData, iconData);
  });

  it('truncates overlong titles and descriptions with diagnostics', () => {
    const result = validateDocument(
      doc({ chips: [chip({ title: 'x'.repeat(61), description: 'y'.repeat(201) })] }),
      SOURCE,
    );
    assert.strictEqual(result.chips[0].title.length, 60);
    assert.strictEqual(result.chips[0].description.length, 200);
    assert.match(result.diagnostics.map((d) => d.message).join(' '), /truncated/);
  });

  it('skips a whole document with an unknown apiVersion', () => {
    const result = validateDocument(
      doc({ apiVersion: 'launchpad.scout.xnat.org/v2', chips: [chip()] }),
      SOURCE,
    );
    assert.strictEqual(result.chips.length, 0);
    assert.match(result.diagnostics[0].message, /unknown document type/);
  });

  it('skips a whole document with an unknown kind', () => {
    const result = validateDocument(doc({ kind: 'Chip', chips: [chip()] }), SOURCE);
    assert.strictEqual(result.chips.length, 0);
    assert.match(result.diagnostics[0].message, /unknown document type/);
  });

  it('warns on unknown fields without dropping the chip', () => {
    const result = validateDocument(doc({ chips: [chip({ bogus: true })] }), SOURCE);
    assert.strictEqual(result.chips.length, 1);
    assert.match(result.diagnostics[0].message, /unknown field "bogus"/);
  });

  it('rejects the later of two chips with the same id in one document', () => {
    const result = validateDocument(
      doc({ chips: [chip({ title: 'First' }), chip({ title: 'Second' })] }),
      SOURCE,
    );
    assert.strictEqual(result.chips.length, 1);
    assert.strictEqual(result.chips[0].title, 'First');
    assert.match(result.diagnostics[0].message, /duplicate chip id/);
  });

  it('defaults group maxColumns by layout', () => {
    const result = validateDocument(
      doc({
        groups: [
          { id: 'a', title: 'A', layout: 'cards' },
          { id: 'b', title: 'B', layout: 'tiles' },
          { id: 'c', title: 'C', layout: 'rows' },
        ],
      }),
      SOURCE,
    );
    assert.deepStrictEqual(
      result.groups.map((g) => g.maxColumns),
      [3, 2, 1],
    );
  });

  it('drops an invalid footerLink but keeps the group', () => {
    const result = validateDocument(
      doc({ groups: [{ id: 'a', title: 'A', footerLink: { text: 'Docs', url: 'javascript:x' } }] }),
      SOURCE,
    );
    assert.strictEqual(result.groups.length, 1);
    assert.strictEqual(result.groups[0].footerLink, undefined);
    assert.match(result.diagnostics[0].message, /invalid footerLink/);
  });

  it('rejects a chip whose enabled or audience is invalid (visibility fails closed)', () => {
    for (const overrides of [{ enabled: 'no' }, { enabled: 0 }, { audience: 'adminstrator' }]) {
      const result = validateDocument(doc({ chips: [chip(overrides)] }), SOURCE);
      assert.strictEqual(result.chips.length, 0, JSON.stringify(overrides));
      assert.match(result.diagnostics[0].message, /chip skipped/);
    }
  });

  it('rejects protocol-relative link paths', () => {
    for (const link of [
      { path: '//evil.example.com/login' },
      { path: '/\\evil.example.com' },
      { subdomain: 'demo', path: '//x' },
    ]) {
      const result = validateDocument(doc({ chips: [chip({ link })] }), SOURCE);
      assert.strictEqual(result.chips.length, 0, JSON.stringify(link));
      assert.match(result.diagnostics[0].message, /chip skipped/);
    }
  });

  it('drops a protocol-relative footerLink url but keeps the group', () => {
    const result = validateDocument(
      doc({
        groups: [{ id: 'a', title: 'A', footerLink: { text: 'Docs', url: '//evil.example.com' } }],
      }),
      SOURCE,
    );
    assert.strictEqual(result.groups.length, 1);
    assert.strictEqual(result.groups[0].footerLink, undefined);
    assert.match(result.diagnostics[0].message, /invalid footerLink/);
  });

  it('treats a present-but-empty chips or groups key as an empty list', () => {
    const result = validateDocument(
      doc({ chips: null, groups: [{ id: 'a', title: 'A' }] }),
      SOURCE,
    );
    assert.deepStrictEqual(result.diagnostics, []);
    assert.strictEqual(result.groups.length, 1);
  });
});

describe('parseCatalogText', () => {
  it('parses a valid YAML document', () => {
    const text = [
      `apiVersion: ${CATALOG_API_VERSION}`,
      `kind: ${CATALOG_KIND}`,
      'chips:',
      '  - id: demo',
      '    title: Demo',
      '    link: { subdomain: demo, path: /start }',
    ].join('\n');
    const result = parseCatalogText(text, SOURCE);
    assert.strictEqual(result.chips.length, 1);
    assert.deepStrictEqual(result.chips[0].link, { subdomain: 'demo', path: '/start' });
  });

  it('reports YAML syntax errors with positions and loses only that document', () => {
    const result = parseCatalogText('chips: [unclosed', SOURCE);
    assert.strictEqual(result.chips.length, 0);
    assert.ok(result.diagnostics.length > 0, 'expected at least one diagnostic');
    assert.match(result.diagnostics[0].message, /YAML parse error/);
    assert.match(result.diagnostics[0].message, /line \d+/);
  });

  it('parses every document in a multi-document key', () => {
    const text = [
      `apiVersion: ${CATALOG_API_VERSION}`,
      `kind: ${CATALOG_KIND}`,
      'chips:',
      '  - { id: one, title: One, link: { path: /one } }',
      '---',
      `apiVersion: ${CATALOG_API_VERSION}`,
      `kind: ${CATALOG_KIND}`,
      'chips:',
      '  - { id: two, title: Two, link: { path: /two } }',
    ].join('\n');
    const result = parseCatalogText(text, SOURCE);
    assert.deepStrictEqual(
      result.chips.map((c) => c.id),
      ['one', 'two'],
    );
    assert.deepStrictEqual(
      result.chips.map((c) => c.source),
      [`${SOURCE} (document 1)`, `${SOURCE} (document 2)`],
    );
  });

  it('a syntax error in one document of a multi-document key costs only that document', () => {
    const text = [
      `apiVersion: ${CATALOG_API_VERSION}`,
      `kind: ${CATALOG_KIND}`,
      'chips:',
      '  - { id: one, title: One, link: { path: /one } }',
      '---',
      'chips: [unclosed',
    ].join('\n');
    const result = parseCatalogText(text, SOURCE);
    assert.deepStrictEqual(
      result.chips.map((c) => c.id),
      ['one'],
    );
    assert.ok(
      result.diagnostics.some((d) => d.message.includes('YAML parse error')),
      'expected a YAML parse error diagnostic',
    );
  });

  it('ignores an empty document left by a trailing separator', () => {
    const text = [
      `apiVersion: ${CATALOG_API_VERSION}`,
      `kind: ${CATALOG_KIND}`,
      'chips:',
      '  - { id: one, title: One, link: { path: /one } }',
      '---',
      '',
    ].join('\n');
    const result = parseCatalogText(text, SOURCE);
    assert.deepStrictEqual(
      result.chips.map((c) => c.id),
      ['one'],
    );
    assert.deepStrictEqual(result.diagnostics, []);
  });
});
