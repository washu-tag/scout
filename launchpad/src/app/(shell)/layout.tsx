import { headers } from 'next/headers';
import ShellClient from '@/components/shell/ShellClient';
import { requestModel } from '@/lib/catalog/request';
import { shellSections } from '@/lib/catalog/shell';
import { frameAllow } from '@/lib/csp';
import { getDocsUrl } from '@/lib/docsUrl';

export const dynamic = 'force-dynamic';

export default async function ShellLayout({ children }: { children: React.ReactNode }) {
  const requestHeaders = await headers();
  const host = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host') ?? '';
  return (
    <ShellClient
      sections={shellSections(await requestModel())}
      environment={process.env.SCOUT_ENV ?? 'local'}
      docsUrl={getDocsUrl()}
      frameAllow={frameAllow(host)}
    >
      {children}
    </ShellClient>
  );
}
