// Resolved at request time in server components; falls back to the latest
// published docs when SCOUT_DOCS_URL is not set (e.g. local dev).
export function getDocsUrl(): string {
  return process.env.SCOUT_DOCS_URL || 'https://washu-scout.readthedocs.io/en/latest';
}
