import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';

/**
 * API endpoint to read playbook definitions from mounted ConfigMap.
 * Returns an array of playbook definitions for the Launchpad to render.
 *
 * The playbooks.json file is mounted from a Kubernetes ConfigMap at /app/config/playbooks.json
 */

// Disable caching so we always read the latest ConfigMap data
export const dynamic = 'force-dynamic';
export const revalidate = 0;

interface PlaybookDefinition {
  id: string;
  title: string;
  description: string;
  notebook: string;
  icon: string;
  color: string;
}

const CONFIG_PATH = '/app/config/playbooks.json';

export async function GET() {
  try {
    // Check if config file exists
    const configPath = path.resolve(CONFIG_PATH);

    try {
      await fs.access(configPath);
    } catch {
      // File doesn't exist - return empty array (no dynamic playbooks)
      console.log('[Playbooks API] No playbooks.json found, returning empty array');
      return NextResponse.json([], {
        headers: {
          'Cache-Control': 'no-store, no-cache, must-revalidate',
        },
      });
    }

    // Read and parse the config file
    const fileContent = await fs.readFile(configPath, 'utf-8');
    const playbooks: PlaybookDefinition[] = JSON.parse(fileContent);

    console.log(`[Playbooks API] Loaded ${playbooks.length} playbooks from config`);

    return NextResponse.json(playbooks, {
      headers: {
        'Cache-Control': 'no-store, no-cache, must-revalidate',
      },
    });
  } catch (error) {
    console.error('[Playbooks API] Error reading playbooks config:', error);
    // Return empty array on error to gracefully degrade
    return NextResponse.json([], {
      headers: {
        'Cache-Control': 'no-store, no-cache, must-revalidate',
      },
    });
  }
}
