import ShellClient from '@/components/shell/ShellClient';
import { requestModel, requestOrigin, unframedHosts } from '@/lib/catalog/request';
import { shellSections } from '@/lib/catalog/shell';
import { getDocsUrl } from '@/lib/docsUrl';
import { notificationsEnabled } from '@/lib/notifications/settings';

export const dynamic = 'force-dynamic';

export default async function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <ShellClient
      sections={shellSections(await requestModel())}
      origin={await requestOrigin()}
      unframed={unframedHosts()}
      environment={process.env.SCOUT_ENV ?? 'local'}
      docsUrl={getDocsUrl()}
      notices={notificationsEnabled()}
    >
      {children}
    </ShellClient>
  );
}
