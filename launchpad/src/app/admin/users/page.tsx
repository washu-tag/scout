import UsersClient from './UsersClient';
import { getDocsUrl } from '@/lib/docsUrl';

// Read auth/session per request; never statically prerender this admin view.
export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'User Approval · Scout',
};

export default function ApprovalsPage() {
  // Mirror the home page so the console breadcrumb shows the same environment.
  const scoutEnv = process.env.SCOUT_ENV;
  return <UsersClient scoutEnv={scoutEnv} docsUrl={getDocsUrl()} />;
}
