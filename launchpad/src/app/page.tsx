import HomeClient from './HomeClient';

// Force dynamic rendering so env vars are read at request time, not build time
export const dynamic = 'force-dynamic';

// Server Component - reads environment variables at runtime
export default function Home() {
  const enableChat = process.env.ENABLE_CHAT === 'true';
  const showPlaybooks = process.env.SHOW_PLAYBOOKS === 'true';

  console.log('[Scout Server] Environment variables:', {
    enableChat,
    showPlaybooks,
    rawEnableChat: process.env.ENABLE_CHAT,
    rawShowPlaybooks: process.env.SHOW_PLAYBOOKS,
  });

  return <HomeClient enableChat={enableChat} showPlaybooks={showPlaybooks} />;
}
