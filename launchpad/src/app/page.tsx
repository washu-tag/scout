import { headers } from 'next/headers';
import { getServerSession } from 'next-auth';
import HomeClient from './HomeClient';
import { assemble, type Origin } from '@/lib/catalog/assemble';
import { loadCatalog } from '@/lib/catalog/load';
import { authOptions } from '@/lib/auth';
import { getDocsUrl } from '@/lib/docsUrl';

// Force dynamic rendering: the catalog, the session, and the request host are
// all read at request time.
export const dynamic = 'force-dynamic';

// Subdomain chip links resolve against the request's own host — what Traefik
// forwards is what the browser sees (ADR 0034). The configured NextAuth URL
// is only the header-less fallback.
async function requestOrigin(): Promise<Origin> {
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

export default async function Home() {
  const origin = await requestOrigin();
  // Dev-only override, mirroring the client-side NEXT_PUBLIC_DEV_ADMIN gate.
  const devAdmin =
    process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_DEV_ADMIN === 'true';
  const session = await getServerSession(authOptions);
  const isAdmin = devAdmin || session?.user?.isAdmin === true;

  const catalog = await loadCatalog();
  const model = assemble(catalog, { origin, isAdmin });

  return (
    <HomeClient
      model={model}
      scoutEnv={process.env.SCOUT_ENV}
      deployerName={process.env.DEPLOYER_NAME}
      docsUrl={getDocsUrl()}
    />
  );
}
