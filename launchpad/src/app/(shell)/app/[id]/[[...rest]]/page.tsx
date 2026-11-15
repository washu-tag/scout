import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { requestModel } from '@/lib/catalog/request';
import { embeddedChip, shellSections } from '@/lib/catalog/shell';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ id: string; rest?: string[] }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { id } = await params;
  const chip = embeddedChip(shellSections(await requestModel()), id);
  return chip ? { title: `${chip.title} · Scout` } : {};
}

// The shell layout renders the app's frame; this route only guards the id.
export default async function AppPage({ params }: Params) {
  const { id } = await params;
  if (!embeddedChip(shellSections(await requestModel()), id)) redirect('/');
  return null;
}
