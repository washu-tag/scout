const HOSTNAME = /^[a-z0-9.-]+$/i;

// The launchpad's Content-Security-Policy: scripts run only with this response's
// nonce (Next.js attaches it to its own), and only Scout's subdomains may be framed.
export function contentSecurityPolicy({
  nonce,
  host,
  dev,
}: {
  nonce: string;
  host: string;
  dev: boolean;
}): string {
  const hostname = host.split(':')[0];
  const frames = HOSTNAME.test(hostname) ? `https://*.${hostname}` : "'none'";
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
