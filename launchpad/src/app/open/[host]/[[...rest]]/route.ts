import { getServerSession } from 'next-auth';
import { NextRequest, NextResponse } from 'next/server';
import { authOptions } from '@/lib/auth';
import { requestModel, requestOrigin } from '@/lib/catalog/request';
import {
  chipForUrl,
  openTarget,
  shellLocation,
  shellPath,
  shellSections,
} from '@/lib/catalog/shell';

export const dynamic = 'force-dynamic';

// The edge sends top-level page loads of a Scout service here, as
// /open/<service host>/<path>, and so does a service page opened outside the
// launchpad. The app that owns the URL opens in the shell at that path.
export async function GET(req: NextRequest) {
  const origin = await requestOrigin();
  const target = openTarget(req.nextUrl.pathname, req.nextUrl.search, origin);
  if (!target) {
    return new NextResponse(null, { status: 307, headers: { Location: '/' } });
  }

  const signedIn = Boolean(await getServerSession(authOptions));
  const chip = chipForUrl(shellSections(await requestModel({ anyAudience: !signedIn })), target);
  if (chip) {
    return new NextResponse(null, {
      status: 307,
      headers: { Location: shellLocation(chip, target.href) ?? shellPath(chip.id) },
    });
  }

  // Scout services open only inside the launchpad, so a URL no app owns goes home.
  return new NextResponse(null, { status: 307, headers: { Location: '/' } });
}
