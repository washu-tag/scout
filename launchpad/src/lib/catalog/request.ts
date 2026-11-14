import { getServerSession } from 'next-auth';
import { headers } from 'next/headers';
import { assemble, type Origin } from '@/lib/catalog/assemble';
import { loadCatalog } from '@/lib/catalog/load';
import type { RenderModel } from '@/lib/catalog/types';
import { authOptions } from '@/lib/auth';

// Subdomain chip links resolve against the request's own host — what Traefik
// forwards is what the browser sees (ADR 0034). The configured NextAuth URL
// is only the header-less fallback.
export async function requestOrigin(): Promise<Origin> {
  const requestHeaders = await headers();
  const host = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host');
  const forwardedProto = requestHeaders.get('x-forwarded-proto');
  if (host) {
    const insecure =
      forwardedProto === 'http' || (!forwardedProto && process.env.NODE_ENV === 'development');
    return { protocol: insecure ? 'http' : 'https', host };
  }
  try {
    const fallback = new URL(process.env.NEXTAUTH_URL ?? '');
    return { protocol: fallback.protocol === 'http:' ? 'http' : 'https', host: fallback.host };
  } catch {
    return { protocol: 'https', host: 'localhost:3000' };
  }
}

// The catalog as this request's viewer sees it: audience-filtered, links
// resolved against the request host.
export async function requestModel(): Promise<RenderModel> {
  const origin = await requestOrigin();
  // Dev-only override, mirroring the client-side NEXT_PUBLIC_DEV_ADMIN gate.
  const devAdmin =
    process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_DEV_ADMIN === 'true';
  const session = await getServerSession(authOptions);
  const isAdmin = devAdmin || session?.user?.isAdmin === true;
  return assemble(await loadCatalog(), { origin, isAdmin });
}
