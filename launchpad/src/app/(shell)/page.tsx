import HomeClient from './HomeClient';
import { requestModel } from '@/lib/catalog/request';

// Force dynamic rendering: the catalog, the session, and the request host are
// all read at request time.
export const dynamic = 'force-dynamic';

export default async function Home() {
  return <HomeClient model={await requestModel()} deployerName={process.env.DEPLOYER_NAME} />;
}
