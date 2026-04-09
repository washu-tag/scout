import type { NextConfig } from 'next';
import path from 'path';

const nextConfig: NextConfig = {
  basePath: '',
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname, './'),
  poweredByHeader: false,
};

export default nextConfig;
