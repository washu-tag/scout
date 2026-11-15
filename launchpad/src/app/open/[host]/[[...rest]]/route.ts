import { NextRequest, NextResponse } from 'next/server';
import { requestModel, requestOrigin } from '@/lib/catalog/request';
import {
  chipForUrl,
  openTarget,
  shellLocation,
  shellPath,
  shellSections,
} from '@/lib/catalog/shell';

export const dynamic = 'force-dynamic';

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

// The edge sends links into a Scout service that arrive from outside Scout
// (bookmarks, shared links) here, as /open/<service host>/<path>. A framed app
// opens in the shell at that path; anything else opens on its own.
export async function GET(req: NextRequest) {
  const origin = await requestOrigin();
  const target = openTarget(req.nextUrl.pathname, req.nextUrl.search, origin);
  if (!target) {
    return new NextResponse(null, { status: 307, headers: { Location: '/' } });
  }

  const chip = chipForUrl(shellSections(await requestModel()), target);
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
