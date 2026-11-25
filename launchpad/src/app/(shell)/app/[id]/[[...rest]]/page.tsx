import type { Metadata } from 'next';
import { getServerSession } from 'next-auth';
import { authOptions } from '@/lib/auth';
import { requestModel } from '@/lib/catalog/request';
import { embeddedChip, shellSections } from '@/lib/catalog/shell';
import { ReloadHome } from '@/components/shell/ReloadHome';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string; rest?: string[] }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { id } = await params;
  const chip = embeddedChip(shellSections(await requestModel()), id);
  return chip ? { title: `${chip.title} · Scout` } : {};
}

// The shell layout renders the app's frame; this route only guards the id. Without a
// session the shell signs in first and comes back here, so an admin's deep link survives.
// An id the catalog no longer has may come from a rail drawn before the app left it, and
// client navigation keeps that rail, so home is loaded afresh rather than redirected to.
export default async function AppPage({ params }: Params) {
  const { id } = await params;
  if (!(await getServerSession(authOptions))) return null;
  if (!embeddedChip(shellSections(await requestModel()), id)) return <ReloadHome />;
  return null;
}
