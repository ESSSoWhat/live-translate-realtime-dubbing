/**
 * Live Translate Backend Sync Service
 *
 * Web module for syncing Wix member data to Live Translate backend.
 * Call these functions from frontend pages after member login.
 */

import { customTrigger } from '@wix/automations';

const BACKEND_URL = 'https://api.livetranslate.net';
const WIX_SYNC_SECRET = '5fb67a259259e178e64f04321d044ba0dfc9d2fda583de1b3a09d39a7d93c08a';
const AUTOMATION_TRIGGER_ID = '376cab86-5237-40e8-b0fa-cabfbf63ba9f';

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
    const response = await fetch(`${BACKEND_URL}/api/v1/billing/wix/sync`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Wix-Sync-Secret': WIX_SYNC_SECRET,
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
    // Note: the automation permission scope `AUTOMATIONS.TRIGGER_WEBHOOK` is required.
    try {
      await customTrigger.runTrigger({
        triggerId: AUTOMATION_TRIGGER_ID,
        payload: {
          email: memberInfo.email,
          tier: memberInfo.tier || 'free',
        },
      });
    } catch (automationError) {
      // Don't block backend sync; automation failure shouldn't prevent API key retrieval.
      console.warn('Wix automation trigger failed:', automationError);
    }

    return { success: true };
  } catch (error) {
    return { success: false, error: `Network error: ${error}` };
  }
}

/**
 * Get or create API key for member
 * Call this after member logs in to display their API key
 */
export async function getApiKey(email: string): Promise<ApiKeyResult> {
  try {
    const response = await fetch(`${BACKEND_URL}/api/v1/auth/api-key`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Wix-Sync-Secret': WIX_SYNC_SECRET,
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
    return { success: false, error: `Network error: ${error}` };
  }
}
