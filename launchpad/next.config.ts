import type { NextConfig } from 'next';
import path from 'path';

const nextConfig: NextConfig = {
  basePath: '',
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname, './'),
  poweredByHeader: false,
  // /open/<subdomain>/<path> carries a service path verbatim, trailing slash included.
  skipTrailingSlashRedirect: true,
};

export default nextConfig;
