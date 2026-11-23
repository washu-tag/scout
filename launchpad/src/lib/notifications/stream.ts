import type { Summary } from './store';

const KEEP_ALIVE_MS = 25_000;

export interface StreamSource {
  first: Summary;
  summary: () => Promise<Summary>;
  subscribe: (onChange: () => void) => () => void;
  // false once the session behind the stream has ended.
  sessionLive: () => Promise<boolean>;
  signal: AbortSignal;
  keepAliveMs?: number;
}

// Server-Sent Events for one reader: a `counts` event whenever their summary
// changes, a comment line between them, and a `session` event before closing
// when their session has ended.
export function noticeStream(source: StreamSource): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let stop = () => {};
  return new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false;
      let last = '';
      let running = false;
      let again = false;
      const write = (text: string) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(text));
        } catch {
          close();
        }
      };
      const counts = (summary: Summary) => {
        const data = JSON.stringify(summary);
        if (data === last) return;
        last = data;
        write(`event: counts\ndata: ${data}\n\n`);
      };
      const refresh = async () => {
        if (running) {
          again = true;
          return;
        }
        running = true;
        try {
          do {
            again = false;
            counts(await source.summary());
          } while (again && !closed);
        } catch (err) {
          console.error('[notifications] stream refresh failed', String(err));
          close();
        } finally {
          running = false;
        }
      };
      let checking = false;
      const keepAlive = setInterval(async () => {
        write(': keep-alive\n\n');
        if (closed || checking) return;
        checking = true;
        const live = await source
          .sessionLive()
          .catch(() => true)
          .finally(() => {
            checking = false;
          });
        if (!live) {
          write('event: session\ndata: {}\n\n');
          close();
        }
      }, source.keepAliveMs ?? KEEP_ALIVE_MS);
      const unsubscribe = source.subscribe(refresh);
      const close = () => {
        if (closed) return;
        closed = true;
        clearInterval(keepAlive);
        unsubscribe();
        source.signal.removeEventListener('abort', close);
        try {
          controller.close();
        } catch {
          // Already closed by the reader.
        }
      };
      stop = close;
      source.signal.addEventListener('abort', close);
      counts(source.first);
    },
    cancel() {
      stop();
    },
  });
}
