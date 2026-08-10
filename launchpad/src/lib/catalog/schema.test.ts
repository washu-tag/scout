import { describe, expect, it } from 'vitest';
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
    expect(result.diagnostics).toEqual([]);
    expect(result.chips).toHaveLength(1);
    const parsed = result.chips[0];
    expect(parsed).toMatchObject({
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
    expect(result.chips.map((c) => c.id)).toEqual(['good']);
    expect(result.diagnostics).toHaveLength(1);
    expect(result.diagnostics[0]).toMatchObject({ subject: 'bad' });
    expect(result.diagnostics[0].message).toContain('chip skipped');
  });

  it('falls back per-field on bad presentation values, with diagnostics', () => {
    const result = validateDocument(
      doc({ chips: [chip({ icon: 'not-an-icon', tone: 'mauve', weight: 'heavy' })] }),
      SOURCE,
    );
    expect(result.chips).toHaveLength(1);
    expect(result.chips[0]).toMatchObject({ icon: 'app', tone: 'indigo', weight: 100 });
    const fields = result.diagnostics.map((d) => d.message);
    expect(fields.some((m) => m.includes('invalid icon'))).toBe(true);
    expect(fields.some((m) => m.includes('invalid tone'))).toBe(true);
    expect(fields.some((m) => m.includes('invalid weight'))).toBe(true);
  });

  it('rejects non-http(s) destinations outright', () => {
    for (const link of [
      { url: 'javascript:alert(1)' },
      { url: 'data:text/html;base64,xx' },
      { url: 'ftp://example.com' },
    ]) {
      const result = validateDocument(doc({ chips: [chip({ link })] }), SOURCE);
      expect(result.chips).toHaveLength(0);
      expect(result.diagnostics[0].message).toContain('chip skipped');
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
      expect(result.chips, JSON.stringify(link)).toHaveLength(1);
    }
    for (const link of [{}, { url: 'https://example.com', subdomain: 'demo' }]) {
      const result = validateDocument(doc({ chips: [chip({ link })] }), SOURCE);
      expect(result.chips, JSON.stringify(link)).toHaveLength(0);
    }
  });

  it('drops an invalid iconData but keeps the chip', () => {
    const result = validateDocument(
      doc({ chips: [chip({ iconData: 'data:text/html;base64,AAAA' })] }),
      SOURCE,
    );
    expect(result.chips).toHaveLength(1);
    expect(result.chips[0].iconData).toBeUndefined();
    expect(result.diagnostics[0].message).toContain('invalid iconData');
  });

  it('accepts a valid image data URI', () => {
    const iconData = 'data:image/png;base64,iVBORw0KGgo=';
    const result = validateDocument(doc({ chips: [chip({ iconData })] }), SOURCE);
    expect(result.chips[0].iconData).toBe(iconData);
  });

  it('truncates overlong titles and descriptions with diagnostics', () => {
    const result = validateDocument(
      doc({ chips: [chip({ title: 'x'.repeat(61), description: 'y'.repeat(201) })] }),
      SOURCE,
    );
    expect(result.chips[0].title).toHaveLength(60);
    expect(result.chips[0].description).toHaveLength(200);
    expect(result.diagnostics.map((d) => d.message).join(' ')).toContain('truncated');
  });

  it('skips a whole document with an unknown apiVersion', () => {
    const result = validateDocument(
      doc({ apiVersion: 'launchpad.scout.xnat.org/v2', chips: [chip()] }),
      SOURCE,
    );
    expect(result.chips).toHaveLength(0);
    expect(result.diagnostics[0].message).toContain('unknown document type');
  });

  it('skips a whole document with an unknown kind', () => {
    const result = validateDocument(doc({ kind: 'Chip', chips: [chip()] }), SOURCE);
    expect(result.chips).toHaveLength(0);
    expect(result.diagnostics[0].message).toContain('unknown document type');
  });

  it('warns on unknown fields without dropping the chip', () => {
    const result = validateDocument(doc({ chips: [chip({ bogus: true })] }), SOURCE);
    expect(result.chips).toHaveLength(1);
    expect(result.diagnostics[0].message).toContain('unknown field "bogus"');
  });

  it('rejects the later of two chips with the same id in one document', () => {
    const result = validateDocument(
      doc({ chips: [chip({ title: 'First' }), chip({ title: 'Second' })] }),
      SOURCE,
    );
    expect(result.chips).toHaveLength(1);
    expect(result.chips[0].title).toBe('First');
    expect(result.diagnostics[0].message).toContain('duplicate chip id');
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
    expect(result.groups.map((g) => g.maxColumns)).toEqual([3, 2, 1]);
  });

  it('drops an invalid footerLink but keeps the group', () => {
    const result = validateDocument(
      doc({ groups: [{ id: 'a', title: 'A', footerLink: { text: 'Docs', url: 'javascript:x' } }] }),
      SOURCE,
    );
    expect(result.groups).toHaveLength(1);
    expect(result.groups[0].footerLink).toBeUndefined();
    expect(result.diagnostics[0].message).toContain('invalid footerLink');
  });

  it('rejects a chip whose enabled or audience is invalid (visibility fails closed)', () => {
    for (const overrides of [{ enabled: 'no' }, { enabled: 0 }, { audience: 'adminstrator' }]) {
      const result = validateDocument(doc({ chips: [chip(overrides)] }), SOURCE);
      expect(result.chips, JSON.stringify(overrides)).toHaveLength(0);
      expect(result.diagnostics[0].message).toContain('chip skipped');
    }
  });

  it('rejects protocol-relative link paths', () => {
    for (const link of [
      { path: '//evil.example.com/login' },
      { path: '/\\evil.example.com' },
      { subdomain: 'demo', path: '//x' },
    ]) {
      const result = validateDocument(doc({ chips: [chip({ link })] }), SOURCE);
      expect(result.chips, JSON.stringify(link)).toHaveLength(0);
      expect(result.diagnostics[0].message).toContain('chip skipped');
    }
  });

  it('drops a protocol-relative footerLink url but keeps the group', () => {
    const result = validateDocument(
      doc({
        groups: [{ id: 'a', title: 'A', footerLink: { text: 'Docs', url: '//evil.example.com' } }],
      }),
      SOURCE,
    );
    expect(result.groups).toHaveLength(1);
    expect(result.groups[0].footerLink).toBeUndefined();
    expect(result.diagnostics[0].message).toContain('invalid footerLink');
  });

  it('treats a present-but-empty chips or groups key as an empty list', () => {
    const result = validateDocument(
      doc({ chips: null, groups: [{ id: 'a', title: 'A' }] }),
      SOURCE,
    );
    expect(result.diagnostics).toEqual([]);
    expect(result.groups).toHaveLength(1);
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
    expect(result.chips).toHaveLength(1);
    expect(result.chips[0].link).toEqual({ subdomain: 'demo', path: '/start' });
  });

  it('reports YAML syntax errors with positions and loses only that document', () => {
    const result = parseCatalogText('chips: [unclosed', SOURCE);
    expect(result.chips).toHaveLength(0);
    expect(result.diagnostics.length).toBeGreaterThan(0);
    expect(result.diagnostics[0].message).toContain('YAML parse error');
    expect(result.diagnostics[0].message).toMatch(/line \d+/);
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
    expect(result.chips.map((c) => c.id)).toEqual(['one', 'two']);
    expect(result.chips.map((c) => c.source)).toEqual([
      `${SOURCE} (document 1)`,
      `${SOURCE} (document 2)`,
    ]);
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
    expect(result.chips.map((c) => c.id)).toEqual(['one']);
    expect(result.diagnostics.some((d) => d.message.includes('YAML parse error'))).toBe(true);
  });
});
