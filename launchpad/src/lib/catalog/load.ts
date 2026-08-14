import { promises as fs } from 'fs';
import path from 'path';
import { parseCatalogText } from './schema';
import type { Catalog, Diagnostic } from './types';

// Server-only module: reads catalog documents from the directories named in
// LAUNCHPAD_CATALOG_DIRS (colon-separated; earlier directories outrank later
// ones for group definitions — the chart lists the mounted catalog first and
// the sidecar-discovered directory second).
//
// The parsed, validated catalog is held as an in-process snapshot keyed by a
// cheap directory signature and re-checked at most once per TTL; concurrent
// requests past the TTL share a single rebuild. A rebuild that fails — or
// finds every configured directory unreadable — serves the previous
// snapshot, and an unreadable directory surfaces as a diagnostic rather than
// silently rendering an empty page (ADR 0034).

const SNAPSHOT_TTL_MS = 10_000;

interface Snapshot {
  signature: string;
  catalog: Catalog;
  checkedAt: number;
}

let snapshot: Snapshot | null = null;
let inflight: Promise<Catalog> | null = null;

export function catalogDirs(): string[] {
  return (process.env.LAUNCHPAD_CATALOG_DIRS ?? '')
    .split(':')
    .map((dir) => dir.trim())
    .filter(Boolean);
}

const YAML_FILE_RE = /\.ya?ml$/i;

interface CatalogFile {
  filePath: string;
  source: string;
  rank: number;
}

interface DirScan {
  // YAML documents to read, in deterministic dir-then-name order.
  files: CatalogFile[];
  // Unreadable directories and non-YAML keys.
  diagnostics: Diagnostic[];
  // Cheap change detector over every visible file. Non-YAML keys count too:
  // they contribute diagnostics, so their appearance must trigger a rebuild.
  signature: string;
  readableDirs: number;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// One walk serves both the signature and the read list, so the change
// detector and the parser cannot disagree about which files count.
async function scanDirs(dirs: string[]): Promise<DirScan> {
  const scan: DirScan = { files: [], diagnostics: [], signature: '', readableDirs: 0 };
  const parts: string[] = [];
  for (const [rank, dir] of dirs.entries()) {
    let names: string[];
    try {
      names = await fs.readdir(dir);
    } catch (err) {
      parts.push(`${dir}:absent`);
      scan.diagnostics.push({
        source: path.basename(dir),
        message: `catalog directory ${dir} is not readable: ${errorMessage(err)}`,
      });
      continue;
    }
    scan.readableDirs += 1;
    // Hidden entries are volume-mount internals (..data symlink targets).
    const visible = names.filter((name) => !name.startsWith('.')).sort();
    const stats = await Promise.all(
      visible.map(async (name) => {
        try {
          return { name, stat: await fs.stat(path.join(dir, name)) };
        } catch {
          // Vanished between readdir and stat (sidecar mid-write); the next
          // TTL check settles it.
          return { name, stat: null };
        }
      }),
    );
    for (const { name, stat } of stats) {
      if (!stat) {
        parts.push(`${dir}/${name}:gone`);
        continue;
      }
      if (!stat.isFile()) continue;
      parts.push(`${dir}/${name}:${stat.mtimeMs}:${stat.size}`);
      const source = `${path.basename(dir)}/${name}`;
      if (!YAML_FILE_RE.test(name)) {
        scan.diagnostics.push({
          source,
          message: 'not a .yaml/.yml key; skipped (catalog documents must be YAML)',
        });
        continue;
      }
      scan.files.push({ filePath: path.join(dir, name), source, rank });
    }
  }
  scan.signature = parts.join('\n');
  return scan;
}

async function readDocuments(scan: DirScan): Promise<Catalog> {
  const catalog: Catalog = { chips: [], groups: [], diagnostics: [...scan.diagnostics] };
  const reads = await Promise.all(
    scan.files.map(
      async (file): Promise<{ file: CatalogFile; text: string | null; err?: unknown }> => {
        try {
          return { file, text: await fs.readFile(file.filePath, 'utf-8') };
        } catch (err) {
          return { file, text: null, err };
        }
      },
    ),
  );
  for (const { file, text, err } of reads) {
    if (text === null) {
      catalog.diagnostics.push({
        source: file.source,
        message: `unreadable: ${errorMessage(err)}`,
      });
      continue;
    }
    const parsed = parseCatalogText(text, file.source, file.rank);
    catalog.chips.push(...parsed.chips);
    catalog.groups.push(...parsed.groups);
    catalog.diagnostics.push(...parsed.diagnostics);
  }
  return catalog;
}

export async function loadCatalog(now = Date.now()): Promise<Catalog> {
  const dirs = catalogDirs();
  if (dirs.length === 0) {
    return {
      chips: [],
      groups: [],
      diagnostics: [
        {
          source: 'catalog-loader',
          message:
            'LAUNCHPAD_CATALOG_DIRS is not set; point it at one or more catalog directories (fixtures/catalog for local dev)',
        },
      ],
    };
  }

  if (snapshot && now - snapshot.checkedAt < SNAPSHOT_TTL_MS) {
    return snapshot.catalog;
  }

  // Single-flight: concurrent requests arriving past the TTL share one scan
  // and rebuild instead of each running their own.
  inflight ??= refresh(dirs, now).finally(() => {
    inflight = null;
  });
  return inflight;
}

async function refresh(dirs: string[], now: number): Promise<Catalog> {
  try {
    const scan = await scanDirs(dirs);
    if (snapshot && scan.signature === snapshot.signature) {
      snapshot.checkedAt = now;
      return snapshot.catalog;
    }
    if (snapshot && scan.readableDirs === 0) {
      // Every configured directory just became unreadable — more likely a
      // transient mount problem than a genuinely empty catalog. Serve stale;
      // the signature difference rebuilds on recovery.
      console.error('[catalog] no catalog directory readable; serving previous snapshot');
      snapshot.checkedAt = now;
      return snapshot.catalog;
    }
    const catalog = await readDocuments(scan);
    snapshot = { signature: scan.signature, catalog, checkedAt: now };
    for (const diagnostic of catalog.diagnostics) {
      console.warn(
        `[catalog] ${diagnostic.source}${diagnostic.subject ? ` (${diagnostic.subject})` : ''}: ${diagnostic.message}`,
      );
    }
    return catalog;
  } catch (err) {
    console.error('[catalog] rebuild failed; serving previous snapshot', err);
    if (snapshot) {
      snapshot.checkedAt = now;
      return snapshot.catalog;
    }
    return emptyWithError(err);
  }
}

function emptyWithError(err: unknown): Catalog {
  const diagnostics: Diagnostic[] = [
    {
      source: 'catalog-loader',
      message: `catalog could not be read: ${errorMessage(err)}`,
    },
  ];
  return { chips: [], groups: [], diagnostics };
}

// Test hook: the snapshot is module state by design (one per server process).
export function resetCatalogSnapshotForTests(): void {
  snapshot = null;
  inflight = null;
}
