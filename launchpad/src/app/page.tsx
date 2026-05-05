import HomeClient from './HomeClient';

// Force dynamic rendering so env vars are read at request time, not build time
export const dynamic = 'force-dynamic';

// Server Component - reads environment variables at runtime
export default function Home() {
  const enableChat = process.env.ENABLE_CHAT === 'true';
  const enablePlaybooks = process.env.ENABLE_PLAYBOOKS === 'true';
  const scoutEnv = process.env.SCOUT_ENV;

  console.log('[Scout Server] Environment variables:', {
    enableChat,
    enablePlaybooks,
    scoutEnv,
  });

  return (
    <HomeClient enableChat={enableChat} enablePlaybooks={enablePlaybooks} scoutEnv={scoutEnv} />
  );
}
