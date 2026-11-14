import { redirect } from 'next/navigation';
import { requestModel } from '@/lib/catalog/request';
import { embeddedChip, shellSections } from '@/lib/catalog/shell';

export const dynamic = 'force-dynamic';

// The shell layout renders the app's frame; this route only guards the id.
export default async function AppPage({
  params,
}: {
  params: Promise<{ id: string; rest?: string[] }>;
}) {
  const { id } = await params;
  if (!embeddedChip(shellSections(await requestModel()), id)) redirect('/');
  return null;
}
