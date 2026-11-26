import { promises as fs } from 'fs';
import { scanDirs } from '@/lib/catalog/load';
import type { Diagnostic } from '@/lib/catalog/types';
import { indexSenders, parseSenderText, type Sender } from './registration';
import { senderDirs } from './settings';

// Sender registrations from LAUNCHPAD_NOTIFICATION_SENDER_DIRS, held as a
// snapshot the way the catalog is (ADR 0034): rescanned at most once per TTL,
// rebuilt only when the directories change, and served stale when a rebuild
// fails.

const SNAPSHOT_TTL_MS = 10_000;

interface Snapshot {
  signature: string;
  senders: Map<string, Sender>;
  checkedAt: number;
}

let snapshot: Snapshot | null = null;
let inflight: Promise<Map<string, Sender>> | null = null;

export async function loadSenders(now = Date.now()): Promise<Map<string, Sender>> {
  if (snapshot && now - snapshot.checkedAt < SNAPSHOT_TTL_MS) return snapshot.senders;
  inflight ??= refresh(senderDirs(), now).finally(() => {
    inflight = null;
  });
  return inflight;
}

async function refresh(dirs: string[], now: number): Promise<Map<string, Sender>> {
  try {
    const scan = await scanDirs(dirs, 'notification sender');
    if (snapshot && (scan.signature === snapshot.signature || scan.readableDirs === 0)) {
      snapshot.checkedAt = now;
      return snapshot.senders;
    }
    const senders: Sender[] = [];
    const diagnostics: Diagnostic[] = [...scan.diagnostics];
    for (const file of scan.files) {
      try {
        const parsed = parseSenderText(await fs.readFile(file.filePath, 'utf-8'), file.source);
        senders.push(...parsed.senders);
        diagnostics.push(...parsed.diagnostics);
      } catch (err) {
        diagnostics.push({ source: file.source, message: `unreadable: ${String(err)}` });
      }
    }
    const { byClient, diagnostics: duplicates } = indexSenders(senders);
    for (const diagnostic of [...diagnostics, ...duplicates]) {
      console.warn(
        `[notifications] ${diagnostic.source}${diagnostic.subject ? ` (${diagnostic.subject})` : ''}: ${diagnostic.message}`,
      );
    }
    snapshot = { signature: scan.signature, senders: byClient, checkedAt: now };
    return byClient;
  } catch (err) {
    console.error('[notifications] sender registrations could not be read', err);
    if (snapshot) {
      snapshot.checkedAt = now;
      return snapshot.senders;
    }
    return new Map();
  }
}

export function resetSenderSnapshotForTests(): void {
  snapshot = null;
  inflight = null;
}
