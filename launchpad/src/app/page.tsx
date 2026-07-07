import HomeClient from './HomeClient';
import { getDocsUrl } from '@/lib/docsUrl';

// Force dynamic rendering so env vars are read at request time, not build time
export const dynamic = 'force-dynamic';

// Server Component - reads environment variables at runtime
export default function Home() {
  const enableChat = process.env.ENABLE_CHAT === 'true';
  const enablePlaybooks = process.env.ENABLE_PLAYBOOKS === 'true';
  // Default true so deployments still running an in-cluster MinIO (e.g. on-prem)
  // continue to show the Lake card without setting a new env var. Sites that
  // have cut over to AWS S3 set ENABLE_MINIO=false to hide it.
  const enableMinio = process.env.ENABLE_MINIO !== 'false';
  const scoutEnv = process.env.SCOUT_ENV;
  const deployerName = process.env.DEPLOYER_NAME;

  return (
    <HomeClient
      enableChat={enableChat}
      enablePlaybooks={enablePlaybooks}
      enableMinio={enableMinio}
      scoutEnv={scoutEnv}
      deployerName={deployerName}
      docsUrl={getDocsUrl()}
    />
  );
}
