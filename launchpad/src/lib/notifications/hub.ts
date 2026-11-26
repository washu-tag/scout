import { Client } from 'pg';
import { databaseUrl } from './settings';
import { CHANNEL } from './store';

type Listener = () => void;

const RETRY_MAX_MS = 30_000;

// One LISTEN connection per process, so a send on any replica reaches the
// streams open on this one.
export class NoticeHub {
  private readonly listeners = new Map<string, Set<Listener>>();
  private client: Client | null = null;
  private connecting = false;
  private retries = 0;
  private timer?: NodeJS.Timeout;
  private closed = false;

  constructor(private readonly connectionString: string) {}

  subscribe(username: string, listener: Listener): () => void {
    const set = this.listeners.get(username) ?? new Set();
    set.add(listener);
    this.listeners.set(username, set);
    this.connect();
    return () => {
      set.delete(listener);
      if (set.size === 0) this.listeners.delete(username);
    };
  }

  close(): Promise<void> {
    this.closed = true;
    clearTimeout(this.timer);
    const client = this.client;
    this.client = null;
    return client ? client.end().catch(() => {}) : Promise.resolve();
  }

  private dispatch(usernames: Iterable<string>): void {
    for (const username of usernames) {
      for (const listener of this.listeners.get(username) ?? []) listener();
    }
  }

  private connect(): void {
    if (this.closed || this.client || this.connecting || this.timer) return;
    this.connecting = true;
    const client = new Client({ connectionString: this.connectionString, keepAlive: true });
    let gone = false;
    const lost = (err?: Error) => {
      if (gone) return;
      gone = true;
      if (err) console.error('[notifications] listen connection lost', err.message);
      client.removeAllListeners();
      client.on('error', () => {});
      client.end().catch(() => {});
      this.lost(client);
    };
    client.on('notification', (message) => {
      try {
        const { users } = JSON.parse(message.payload ?? '{}');
        if (Array.isArray(users)) this.dispatch(users.filter((u) => typeof u === 'string'));
      } catch {
        // Not ours.
      }
    });
    client.on('error', lost);
    client.on('end', () => lost());
    client
      .connect()
      .then(() => client.query(`LISTEN ${CHANNEL}`))
      .then(() => {
        if (gone) return;
        this.connecting = false;
        if (this.closed) return lost();
        this.client = client;
        this.retries = 0;
        // Changes made while nobody here was listening.
        this.dispatch([...this.listeners.keys()]);
      })
      .catch(lost);
  }

  private lost(client: Client): void {
    if (this.client === client) this.client = null;
    this.connecting = false;
    if (this.closed || this.timer) return;
    const delay = Math.min(RETRY_MAX_MS, 1000 * 2 ** this.retries++);
    this.timer = setTimeout(() => {
      this.timer = undefined;
      this.connect();
    }, delay);
    this.timer.unref();
  }
}

const shared = globalThis as typeof globalThis & { launchpadNoticeHub?: NoticeHub };

export function noticeHub(): NoticeHub {
  const url = databaseUrl();
  if (!url) throw new Error('LAUNCHPAD_DATABASE_URL is not set');
  shared.launchpadNoticeHub ??= new NoticeHub(url);
  return shared.launchpadNoticeHub;
}
