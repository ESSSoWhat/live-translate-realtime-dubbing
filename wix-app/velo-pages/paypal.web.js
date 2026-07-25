/**
 * Backend Web Module for PayPal checkout.
 *
 * Place this file in your Wix site's backend folder as: backend/paypal.web.js
 *
 * Wraps the FastAPI PayPal endpoints so the Wix front-end can start a checkout:
 *   - POST /api/v1/paypal/orders            (one-time, e.g. early_adopters)
 *   - POST /api/v1/paypal/orders/{id}/capture
 *   - POST /api/v1/paypal/subscriptions     (recurring: starter | pro)
 *   - GET  /api/v1/paypal/config            (is PayPal configured?)
 *
 * The order/subscription/config endpoints are public (no secret). getUsageForMember
 * resolves the member's API key server-side using LT_SYNC_SECRET, then reads /user/me.
 *
 * Config:
 * - BACKEND_URL must match api-key.web.js / sync.web.ts (see wix-app/BACKEND_URL.md).
 * - LT_SYNC_SECRET in Wix Secrets Manager (value == backend WIX_SYNC_SECRET/LT_SYNC_SECRET).
 */

import { Permissions, webMethod } from 'wix-web-module';
import { getSecret } from 'wix-secrets-backend';

const BACKEND_URL = 'https://livetranslatedubtool-production.up.railway.app';

/** Return PayPal public config (configured flag, env, currency, plan ids). */
export const getPayPalConfig = webMethod(
    Permissions.Anyone,
    async () => {
        try {
            const res = await fetch(`${BACKEND_URL}/api/v1/paypal/config`, { method: 'GET' });
            if (!res.ok) return { configured: false };
            return await res.json();
        } catch (e) {
            console.error('PayPal config failed:', e);
            return { configured: false };
        }
    }
);

/**
 * Create a recurring subscription (starter | pro). Returns the PayPal approval URL
 * the member should be redirected to.
 * @returns {{ success: boolean, subscriptionId?: string, approveUrl?: string, error?: string }}
 */
export const createPayPalSubscription = webMethod(
    Permissions.SiteMember,
    async (email, tier) => {
        try {
            const res = await fetch(`${BACKEND_URL}/api/v1/paypal/subscriptions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, tier }),
            });
            if (!res.ok) {
                const txt = await res.text();
                return { success: false, error: `Backend error: ${res.status} ${txt}` };
            }
            const data = await res.json();
            return { success: true, subscriptionId: data.subscription_id, approveUrl: data.approve_url };
        } catch (e) {
            console.error('PayPal subscription failed:', e);
            return { success: false, error: 'Failed to start subscription' };
        }
    }
);

/**
 * Create a one-time order (e.g. early_adopters lifetime). Returns the order id +
 * PayPal approval URL (the "approve" rel link).
 * @returns {{ success: boolean, orderId?: string, approveUrl?: string, error?: string }}
 */
export const createPayPalOrder = webMethod(
    Permissions.SiteMember,
    async (email, tier) => {
        try {
            const res = await fetch(`${BACKEND_URL}/api/v1/paypal/orders`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, tier }),
            });
            if (!res.ok) {
                const txt = await res.text();
                return { success: false, error: `Backend error: ${res.status} ${txt}` };
            }
            const data = await res.json();
            const links = data.links || [];
            const approve = links.find(l => l.rel === 'approve' || l.rel === 'payer-action');
            return { success: true, orderId: data.order_id, approveUrl: approve ? approve.href : null };
        } catch (e) {
            console.error('PayPal order failed:', e);
            return { success: false, error: 'Failed to start payment' };
        }
    }
);

/**
 * Capture an approved one-time order (call on return from PayPal with ?token=ORDERID).
 * On COMPLETED the backend provisions the tier in Supabase.
 * @returns {{ success: boolean, status?: string, error?: string }}
 */
export const capturePayPalOrder = webMethod(
    Permissions.SiteMember,
    async (orderId) => {
        try {
            const res = await fetch(`${BACKEND_URL}/api/v1/paypal/orders/${encodeURIComponent(orderId)}/capture`, {
                method: 'POST',
            });
            if (!res.ok) {
                const txt = await res.text();
                return { success: false, error: `Backend error: ${res.status} ${txt}` };
            }
            const data = await res.json();
            return { success: true, status: data.status };
        } catch (e) {
            console.error('PayPal capture failed:', e);
            return { success: false, error: 'Failed to confirm payment' };
        }
    }
);

/**
 * Record an upgrade-nudge A/B event (best-effort; never throws to the caller).
 * @returns {{ ok: boolean }}
 */
export const recordNudge = webMethod(
    Permissions.Anyone,
    async (variant, action, feature) => {
        try {
            const res = await fetch(`${BACKEND_URL}/api/v1/analytics/nudge`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ variant, action, feature, surface: 'wix' }),
            });
            return { ok: res.ok };
        } catch (e) {
            console.error('recordNudge failed:', e);
            return { ok: false };
        }
    }
);

/**
 * Fetch the member's current usage snapshot (tier + used/limit per feature).
 * Resolves the member's API key server-side (LT_SYNC_SECRET), then reads /user/me.
 * @returns {{ success: boolean, tier?: string, usage?: object, error?: string }}
 */
export const getUsageForMember = webMethod(
    Permissions.SiteMember,
    async (email) => {
        try {
            const secret = await getSecret('LT_SYNC_SECRET');
            if (!secret) return { success: false, error: 'Configuration error' };

            const keyRes = await fetch(`${BACKEND_URL}/api/v1/auth/api-key`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Wix-Sync-Secret': secret },
                body: JSON.stringify({ email }),
            });
            if (!keyRes.ok) return { success: false, error: `API key error: ${keyRes.status}` };
            const keyData = await keyRes.json();
            const apiKey = keyData.api_key;
            if (!apiKey) return { success: false, error: 'No API key' };

            const meRes = await fetch(`${BACKEND_URL}/api/v1/user/me`, {
                method: 'GET',
                headers: { Authorization: `Bearer ${apiKey}` },
            });
            if (!meRes.ok) return { success: false, error: `Usage error: ${meRes.status}` };
            const me = await meRes.json();
            return { success: true, tier: me.tier, usage: me.usage };
        } catch (e) {
            console.error('Usage fetch failed:', e);
            return { success: false, error: 'Failed to fetch usage' };
        }
    }
);
