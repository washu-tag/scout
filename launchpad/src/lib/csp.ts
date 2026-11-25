const HOSTNAME = /^[a-z0-9.-]+$/i;

export function scoutSubdomains(host: string): string | null {
  const hostname = host.split(':')[0];
  return HOSTNAME.test(hostname) ? `https://*.${hostname}` : null;
}

// Per-user JupyterHub servers redirect the frame to another Scout subdomain, so
// delegating only to the frame's src origin would leave them without the clipboard.
export function frameAllow(host: string): string {
  const origins = scoutSubdomains(host);
  return ['clipboard-read', 'clipboard-write', 'fullscreen']
    .map((feature) => (origins ? `${feature} ${origins}` : feature))
    .join('; ');
}

// The launchpad's Content-Security-Policy: a page script runs only with this
// response's nonce (Next.js attaches it to its own), and scripts those load inherit
// that trust ('strict-dynamic'); only Scout's subdomains may be framed. Development
// also allows eval and the dev server's WebSocket.
export function contentSecurityPolicy({
  nonce,
  host,
  dev,
}: {
  nonce: string;
  host: string;
  dev: boolean;
}): string {
  const frames = scoutSubdomains(host) ?? "'none'";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ''}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    `connect-src 'self'${dev ? ' ws:' : ''}`,
    `frame-src ${frames}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(dev ? [] : ['upgrade-insecure-requests']),
  ].join('; ');
}
