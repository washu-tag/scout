import { NextRequest, NextResponse } from 'next/server';
import { embedApps, requestModel, requestOrigin } from '@/lib/catalog/request';
import { chipForUrl, shellLocation, shellPath, shellSections } from '@/lib/catalog/shell';

export const dynamic = 'force-dynamic';

const DNS_LABEL = /^[a-z0-9-]{1,63}$/;

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

// The edge sends links into a Scout service that arrive from outside Scout
// (bookmarks, shared links) here, as /open/<subdomain>/<path>. A framed app opens
// in the shell at that path; anything else opens on its own.
export async function GET(req: NextRequest, { params }: { params: Promise<{ sub: string }> }) {
  const { sub } = await params;
  if (!DNS_LABEL.test(sub)) {
    return new NextResponse(null, { status: 307, headers: { Location: '/' } });
  }
  const origin = await requestOrigin();
  const path = req.nextUrl.pathname.slice(`/open/${sub}`.length) || '/';
  const target = new URL(`${origin.protocol}://${sub}.${origin.host}${path}${req.nextUrl.search}`);

  const chip = chipForUrl(shellSections(await requestModel()), target, embedApps());
  if (chip) {
    return new NextResponse(null, {
      status: 307,
      headers: { Location: shellLocation(chip, target.href) ?? shellPath(chip.id) },
    });
  }

  // A script navigation from this page is same-site, so the edge lets it through
  // instead of sending it back here.
  const href = target.href;
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');
  const html = `<!doctype html><meta charset="utf-8"><title>Opening…</title><script nonce="${nonce}">location.replace(${JSON.stringify(href).replace(/</g, '\\u003c')} + location.hash)</script><a href="${escapeHtml(href)}">Continue</a>`;
  return new NextResponse(html, {
    headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Content-Security-Policy': `default-src 'none'; script-src 'nonce-${nonce}'; base-uri 'none'; frame-ancestors 'none'`,
    },
  });
}
