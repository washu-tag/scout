import UsersClient from './UsersClient';

// Read auth/session per request; never statically prerender this admin view.
export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'User Approval · Scout',
};

export default function ApprovalsPage() {
  return <UsersClient />;
}
