import { promises as fs } from 'fs';
import path from 'path';
import { getDocsUrl } from '@/lib/docsUrl';
import { builtinCatalog } from './builtin';
import { parseCatalogText } from './schema';
import type { Catalog, Diagnostic } from './types';

// Server-only module: reads catalog documents from the directories named in
// LAUNCHPAD_CATALOG_DIRS (colon-separated; earlier directories outrank later
// ones for group definitions — the chart lists the mounted catalog first and
// the sidecar-discovered directory second).
//
// The parsed, validated catalog is held as an in-process snapshot keyed by a
// cheap directory signature and re-checked at most once per TTL. A rebuild
// that fails serves the previous snapshot: the page never goes blank because
// discovery went wrong (ADR 0034).

const SNAPSHOT_TTL_MS = 10_000;

interface Snapshot {
  signature: string;
  catalog: Catalog;
  checkedAt: number;
}

let snapshot: Snapshot | null = null;

export function catalogDirs(): string[] {
  return (process.env.LAUNCHPAD_CATALOG_DIRS ?? '')
    .split(':')
    .map((dir) => dir.trim())
    .filter(Boolean);
}

function builtinFromEnv(): Catalog {
  return builtinCatalog({
    enableChat: process.env.ENABLE_CHAT === 'true',
    enablePlaybooks: process.env.ENABLE_PLAYBOOKS === 'true',
    // Default true so deployments still running an in-cluster MinIO keep the
    // Lake chip without setting a new env var (see helm values for history).
    enableMinio: process.env.ENABLE_MINIO !== 'false',
    docsUrl: getDocsUrl(),
  });
}

const YAML_FILE_RE = /\.ya?ml$/i;

async function dirSignature(dirs: string[]): Promise<string> {
  const parts: string[] = [];
  for (const dir of dirs) {
    let names: string[];
    try {
      names = await fs.readdir(dir);
    } catch {
      parts.push(`${dir}:absent`);
      continue;
    }
    for (const name of names.sort()) {
      try {
        const stat = await fs.stat(path.join(dir, name));
        if (stat.isFile()) parts.push(`${dir}/${name}:${stat.mtimeMs}:${stat.size}`);
      } catch {
        // File vanished between readdir and stat (sidecar mid-write); the
        // next TTL check settles it.
        parts.push(`${dir}/${name}:gone`);
      }
    }
  }
  return parts.join('\n');
}

async function readCatalog(dirs: string[]): Promise<Catalog> {
  const catalog: Catalog = { chips: [], groups: [], diagnostics: [] };
  for (const [rank, dir] of dirs.entries()) {
    let names: string[];
    try {
      names = await fs.readdir(dir);
    } catch {
      // A configured directory that does not exist (yet) is normal: the
      // discovery emptyDir starts empty and the mount may be optional.
      continue;
    }
    for (const name of names.sort()) {
      const filePath = path.join(dir, name);
      let stat;
      try {
        stat = await fs.stat(filePath);
      } catch {
        continue;
      }
      if (!stat.isFile()) continue;
      // Hidden files are volume-mount internals (..data symlink targets).
      if (name.startsWith('.')) continue;
      if (!YAML_FILE_RE.test(name)) {
        catalog.diagnostics.push({
          source: `${path.basename(dir)}/${name}`,
          message: 'not a .yaml/.yml key; skipped (catalog documents must be YAML)',
        });
        continue;
      }
      const source = `${path.basename(dir)}/${name}`;
      try {
        const text = await fs.readFile(filePath, 'utf-8');
        const parsed = parseCatalogText(text, source, rank);
        catalog.chips.push(...parsed.chips);
        catalog.groups.push(...parsed.groups);
        catalog.diagnostics.push(...parsed.diagnostics);
      } catch (err) {
        catalog.diagnostics.push({
          source,
          message: `unreadable: ${err instanceof Error ? err.message : String(err)}`,
        });
      }
    }
  }
  return catalog;
}

export async function loadCatalog(now = Date.now()): Promise<Catalog> {
  const dirs = catalogDirs();
  if (dirs.length === 0) {
    return builtinFromEnv();
  }

  if (snapshot && now - snapshot.checkedAt < SNAPSHOT_TTL_MS) {
    return snapshot.catalog;
  }

  let signature: string;
  try {
    signature = await dirSignature(dirs);
  } catch (err) {
    console.error('[catalog] signature check failed; serving previous snapshot', err);
    if (snapshot) return snapshot.catalog;
    return emptyWithError(err);
  }

  if (snapshot && snapshot.signature === signature) {
    snapshot.checkedAt = now;
    return snapshot.catalog;
  }

  try {
    const catalog = await readCatalog(dirs);
    snapshot = { signature, catalog, checkedAt: now };
    for (const diagnostic of catalog.diagnostics) {
      console.warn(
        `[catalog] ${diagnostic.source}${diagnostic.subject ? ` (${diagnostic.subject})` : ''}: ${diagnostic.message}`,
      );
    }
    return catalog;
  } catch (err) {
    console.error('[catalog] rebuild failed; serving previous snapshot', err);
    if (snapshot) return snapshot.catalog;
    return emptyWithError(err);
  }
}

function emptyWithError(err: unknown): Catalog {
  const diagnostics: Diagnostic[] = [
    {
      source: 'catalog-loader',
      message: `catalog could not be read: ${err instanceof Error ? err.message : String(err)}`,
    },
  ];
  return { chips: [], groups: [], diagnostics };
}

// Test hook: the snapshot is module state by design (one per server process).
export function resetCatalogSnapshotForTests(): void {
  snapshot = null;
}
