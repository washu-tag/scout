// Build an absolute URL to a Scout sibling subdomain (e.g. keycloak.<host>),
// reusing the current page's protocol + host. Client-side only (reads
// window.location), so call it from an effect — not during render/SSR.
export function subdomainUrl(subdomain: string, path: string = ''): string {
  const { protocol, host } = window.location;
  const normalizedPath = path ? (path.startsWith('/') ? path : `/${path}`) : '';
  return `${protocol}//${subdomain}.${host}${normalizedPath}`;
}
