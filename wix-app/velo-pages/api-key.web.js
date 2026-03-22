/**
 * Backend Web Module for API Key Page
 *
 * Place this file in your Wix site's backend folder as: backend/api-key.web.js
 *
 * This module is called from the frontend page to securely get API keys.
 * The WIX_SYNC_SECRET is never exposed to the frontend.
 *
 * Config:
 * - BACKEND_URL: Base URL of your FastAPI backend (e.g. https://api.livetranslate.app).
 *   If APIs live at a different domain, update this. All frontend sync/auth calls use it.
 * - WIX_SYNC_SECRET: Set in Wix Secrets Manager. Must exactly match backend WIX_SYNC_SECRET.
 *   Mismatch between prod/staging will break sync and API-key provisioning.
 */

import { Permissions, webMethod } from 'wix-web-module';
import { getSecret } from 'wix-secrets-backend';

// Base URL of backend (POST /api/v1/billing/wix/sync, POST /api/v1/auth/api-key)
const BACKEND_URL = 'https://api.livetranslate.app';

/**
 * Sync member tier to backend; creates user + API key if new (call early on account load).
 * @param {string} email - Member's email
 * @param {string} [planId] - Wix plan ID if known
 * @param {string} [planName] - Wix plan name if known
 * @returns {Promise<{received: boolean, updated?: boolean, tier?: string}>}
 */
export const syncMemberToBackend = webMethod(
    Permissions.SiteMember,
    async (email, planId, planName) => {
        try {
            const secret = await getSecret('WIX_SYNC_SECRET');
            if (!secret) return { received: false };
            const res = await fetch(`${BACKEND_URL}/api/v1/billing/wix/sync`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Wix-Sync-Secret': secret,
                },
                body: JSON.stringify({ email, plan_id: planId || '', plan_name: planName || '' }),
            });
            if (!res.ok) return { received: false };
            return await res.json();
        } catch (e) {
            console.error('Wix sync failed:', e);
            return { received: false };
        }
    }
);

/**
 * Get API key for the current member
 * @param {string} email - Member's email address
 * @returns {Promise<{success: boolean, apiKey?: string, error?: string}>}
 */
export const getApiKeyForMember = webMethod(
    Permissions.SiteMember,  // Only logged-in members can call this
    async (email) => {
        try {
            // Get secret from Wix Secrets Manager
            const secret = await getSecret('WIX_SYNC_SECRET');

            if (!secret) {
                console.error('WIX_SYNC_SECRET not found in Secrets Manager');
                return { success: false, error: 'Configuration error' };
            }

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
                console.error('Backend error:', response.status, errorText);
                return { success: false, error: `Backend error: ${response.status}` };
            }

            const data = await response.json();
            return {
                success: true,
                apiKey: data.api_key,
                userId: data.user_id,
                tier: data.tier,
            };

        } catch (error) {
            console.error('Error fetching API key:', error);
            return { success: false, error: 'Failed to fetch API key' };
        }
    }
);
