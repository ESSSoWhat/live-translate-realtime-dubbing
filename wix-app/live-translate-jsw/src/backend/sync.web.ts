/**
 * Live Translate Backend Sync Service
 *
 * Web module for syncing Wix member data to Live Translate backend.
 * Call these functions from frontend pages after member login.
 *
 * Required: Set the WIX_SYNC_SECRET environment variable via Wix CLI:
 *   wix env set WIX_SYNC_SECRET <your-secret-value>
 */

import { customTrigger } from '@wix/automations';

// Configuration from environment variables (set via `wix env set`)
const BACKEND_URL = import.meta.env.BACKEND_URL || 'https://api.livetranslate.net';
const WIX_SYNC_SECRET = import.meta.env.WIX_SYNC_SECRET;
const AUTOMATION_TRIGGER_ID = '376cab86-5237-40e8-b0fa-cabfbf63ba9f';

function getSecret(): string {
  if (!WIX_SYNC_SECRET) {
    throw new Error(
      'WIX_SYNC_SECRET not configured. Set it via: wix env set WIX_SYNC_SECRET <value>'
    );
  }
  return WIX_SYNC_SECRET;
}

export interface SyncResult {
  success: boolean;
  error?: string;
}

export interface ApiKeyResult {
  success: boolean;
  apiKey?: string;
  userId?: string;
  tier?: string;
  error?: string;
}

export interface MemberInfo {
  email: string;
  tier?: 'free' | 'starter' | 'pro';
}

/**
 * Sync member tier to Live Translate backend
 * Call this after member logs in or upgrades their plan
 */
export async function syncMemberTier(memberInfo: MemberInfo): Promise<SyncResult> {
  try {
    const secret = getSecret();
    const response = await fetch(`${BACKEND_URL}/api/v1/billing/wix/sync`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Wix-Sync-Secret': secret,
      },
      body: JSON.stringify({
        email: memberInfo.email,
        tier: memberInfo.tier || 'free',
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return { success: false, error: `Backend error: ${response.status} - ${errorText}` };
    }

    // Trigger a Wix Automation custom trigger (best-effort).
    try {
      await customTrigger.runTrigger({
        triggerId: AUTOMATION_TRIGGER_ID,
        payload: {
          email: memberInfo.email,
          tier: memberInfo.tier || 'free',
        },
      });
    } catch (automationError) {
      console.warn('Wix automation trigger failed:', automationError);
    }

    return { success: true };
  } catch (error) {
    return { success: false, error: `Error: ${error}` };
  }
}

/**
 * Get or create API key for member
 * Call this after member logs in to display their API key
 */
export async function getApiKey(email: string): Promise<ApiKeyResult> {
  try {
    const secret = getSecret();
    const response = await fetch(`${BACKEND_URL}/api/v1/auth/api-key`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Wix-Sync-Secret': secret,
      },
      body: JSON.stringify({ email }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return { success: false, error: `Backend error: ${response.status} - ${errorText}` };
    }

    const data = await response.json();
    return {
      success: true,
      apiKey: data.api_key,
      userId: data.user_id,
      tier: data.tier,
    };
  } catch (error) {
    return { success: false, error: `Error: ${error}` };
  }
}
