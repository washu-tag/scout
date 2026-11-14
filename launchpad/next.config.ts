import type { NextConfig } from 'next';
import path from 'path';

const nextConfig: NextConfig = {
  basePath: '',
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname, './'),
  poweredByHeader: false,
  // /open/<service host>/<path> carries a service path verbatim, trailing slash included.
  skipTrailingSlashRedirect: true,
  // The security headers Traefik adds on-prem (ADR 0012), sent by the app itself so
  // edges that can't add headers get them too.
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=(), payment=()',
          },
          { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
        ],
      },
    ];
  },
};

export default nextConfig;
