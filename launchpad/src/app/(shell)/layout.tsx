import ShellClient from '@/components/shell/ShellClient';
import { embedApps, requestModel } from '@/lib/catalog/request';
import { shellSections } from '@/lib/catalog/shell';
import { getDocsUrl } from '@/lib/docsUrl';

export const dynamic = 'force-dynamic';

export default async function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <ShellClient
      sections={shellSections(await requestModel())}
      embedApps={embedApps()}
      environment={process.env.SCOUT_ENV ?? 'local'}
      docsUrl={getDocsUrl()}
    >
      {children}
    </ShellClient>
  );
}
