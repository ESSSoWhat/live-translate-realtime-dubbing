/**
 * Backend Web Module for API Key Page
 *
 * Place this file in your Wix site's backend folder as: backend/api-key.web.js
 *
 * Config:
 * - BACKEND_URL: FastAPI backend base URL
 * - LT_SYNC_SECRET: Wix Secrets Manager (must match backend LT_SYNC_SECRET / WIX_SYNC_SECRET)
 */

import { Permissions, webMethod } from 'wix-web-module';
import { getSecret } from 'wix-secrets-backend';
import { orders } from 'wix-pricing-plans.v2';

const BACKEND_URL = 'https://livetranslatedubtool-production.up.railway.app';

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

export const syncMemberToBackend = webMethod(
    Permissions.SiteMember,
    async (email) => {
        try {
            const secret = await getSecret('LT_SYNC_SECRET');
            if (!secret) return { received: false };
            const { planId, planName, status } = await getMemberPlan();
            const res = await fetch(`${BACKEND_URL}/api/v1/billing/wix/sync`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Wix-Sync-Secret': secret },
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

export const getApiKeyForMember = webMethod(
    Permissions.SiteMember,
    async (email) => {
        try {
            const secret = await getSecret('LT_SYNC_SECRET');
            if (!secret) {
                console.error('LT_SYNC_SECRET not found in Secrets Manager');
                return { success: false, error: 'Configuration error' };
            }
            const response = await fetch(`${BACKEND_URL}/api/v1/auth/api-key`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Wix-Sync-Secret': secret },
                body: JSON.stringify({ email }),
            });
            if (!response.ok) {
                const errorText = await response.text();
                console.error('Backend error:', response.status, errorText);
                return { success: false, error: `Backend error: ${response.status}` };
            }
            const data = await response.json();
            return { success: true, apiKey: data.api_key, userId: data.user_id, tier: data.tier };
        } catch (error) {
            console.error('Error fetching API key:', error);
            return { success: false, error: 'Failed to fetch API key' };
        }
    }
);

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
                headers: { 'Content-Type': 'application/json', 'X-Wix-Sync-Secret': secret },
                body: JSON.stringify({ session_id: sessionId, api_key: apiKey }),
            });
            if (!res.ok) {
                const errorText = await res.text();
                console.error('Handoff store error:', res.status, errorText);
                return { stored: false, error: `Backend error: ${res.status}` };
            }
            // Page checks handoffResult.stored (backend JSON is { ok: true })
            return { stored: true };
        } catch (error) {
            console.error('storeDesktopHandoff failed:', error);
            return { stored: false, error: 'Failed to store handoff' };
        }
    }
);
