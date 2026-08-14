/**
 * Backend Web Module for API Key Page
 *
 * Place this file in your Wix site's backend folder as: backend/api-key.web.js
 *
 * This module is called from the frontend page to securely get API keys.
 * The WIX_SYNC_SECRET is never exposed to the frontend.
 *
 * Config:
 * - BACKEND_URL: Base URL of your FastAPI backend (default matches sync.web.ts — see wix-app/BACKEND_URL.md).
 *   If APIs live at a different domain, update this. All frontend sync/auth calls use it.
 * - LT_SYNC_SECRET: Set in Wix Secrets Manager (name must NOT start with "wix", which
 *   Wix rejects). Its VALUE must exactly match the backend's WIX_SYNC_SECRET env var.
 *   Mismatch between prod/staging will break sync and API-key provisioning.
 */

import { Permissions, webMethod } from 'wix-web-module';
import { getSecret } from 'wix-secrets-backend';
import { orders } from 'wix-pricing-plans.v2';

// Base URL of backend (POST /api/v1/billing/wix/sync, POST /api/v1/auth/api-key)
const BACKEND_URL = 'https://livetranslatedubtool-production.up.railway.app';

/**
 * Get current member's active subscription plan from Wix Pricing Plans.
 * @returns {{ planId: string|null, planName: string|null, status: string|null }}
 */
async function getMemberPlan() {
    try {
        const list = await orders.memberListOrders();
        const ordersList = list?.orders || [];
        const active = ordersList.find(o => o.status === 'ACTIVE') ?? null;
        return {
            planId: active?.planId ?? null,
            planName: active?.planName ?? null,
            status: active?.status ?? null,
        };
    } catch (e) {
        console.warn('Could not fetch member plan:', e);
        return { planId: null, planName: null, status: null };
    }
}

/**
 * Sync member tier to backend using website subscription level.
 * Fetches member's Wix Pricing Plan and syncs to backend so app uses correct usage package.
 * Creates user + API key if new (call early on account load).
 * @param {string} email - Member's email
 * @returns {Promise<{received: boolean, updated?: boolean, tier?: string}>}
 */
export const syncMemberToBackend = webMethod(
    Permissions.SiteMember,
    async (email) => {
        try {
            const secret = await getSecret('LT_SYNC_SECRET');
            if (!secret) return { received: false };
            const { planId, planName, status } = await getMemberPlan();
            const res = await fetch(`${BACKEND_URL}/api/v1/billing/wix/sync`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Wix-Sync-Secret': secret,
                },
                body: JSON.stringify({
                    email,
                    plan_id: planId || undefined,
                    plan_name: planName || undefined,
                    status: status || undefined,
                }),
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
            const secret = await getSecret('LT_SYNC_SECRET');

            if (!secret) {
                console.error('LT_SYNC_SECRET not found in Secrets Manager');
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

/**
 * Store a one-time desktop-login handoff on the backend.
 * The desktop app generated `sessionId`, opened the browser here; after member
 * login we post the member's api_key so the app can poll for it (no localhost redirect).
 * @param {string} sessionId - One-time session id from the desktop app
 * @param {string} apiKey - Member's API key
 * @returns {Promise<{stored: boolean, error?: string}>}
 */
export const storeDesktopHandoff = webMethod(
    Permissions.SiteMember,
    async (sessionId, apiKey) => {
        try {
            if (!sessionId || !apiKey) return { stored: false, error: 'Missing session id or api key' };
            const secret = await getSecret('LT_SYNC_SECRET');
            if (!secret) {
                console.error('LT_SYNC_SECRET not found in Secrets Manager');
                return { stored: false, error: 'Configuration error' };
            }
            const res = await fetch(`${BACKEND_URL}/api/v1/auth/desktop-handoff`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Wix-Sync-Secret': secret,
                },
                body: JSON.stringify({
                    session_id: sessionId,
                    api_key: apiKey,
                }),
            });
            if (!res.ok) {
                const errorText = await res.text();
                console.error('Handoff store error:', res.status, errorText);
                return { stored: false, error: `Backend error: ${res.status}` };
            }
            return await res.json();
        } catch (error) {
            console.error('storeDesktopHandoff failed:', error);
            return { stored: false, error: 'Failed to store handoff' };
        }
    }
);
