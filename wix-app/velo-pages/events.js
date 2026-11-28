/**
 * Backend events — two-way account deletion sync (Wix member -> LiveTranslate app account).
 *
 * Place this file in your Wix site's backend folder as: backend/events.js
 * (add the file if it doesn't exist; if it does, merge this function into it).
 * Backend events do not run in Preview: publish the site to activate it.
 *
 * When a Wix member is deleted, tell the backend. The backend deletes the app account
 * (Supabase auth user + public.users row + usage/voices/handoffs) that is linked to that
 * exact Wix member id. It never matches by email or name.
 *
 * Config:
 * - BACKEND_URL: FastAPI backend base URL (same as api-key.web.js)
 * - LT_SYNC_SECRET: Wix Secrets Manager (same secret api-key.web.js already uses)
 * Backend must have ACCOUNT_DELETE_SYNC_ENABLED=true, otherwise it answers {"status":"disabled"}.
 */

import { getSecret } from 'wix-secrets-backend';

const BACKEND_URL = 'https://livetranslatedubtool-production.up.railway.app';
const ATTEMPTS = 3;

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

// Wix's own docs: https://dev.wix.com/docs/velo/apis/wix-members-backend/events/on-member-deleted
// event = { metadata: { id, entityId, eventTime, triggeredByAnonymizeRequest } }
export async function wixMembers_onMemberDeleted(event) {
    const memberId = event && event.metadata && event.metadata.entityId;
    if (!memberId) {
        console.error('onMemberDeleted: event had no member id');
        return;
    }
    let secret;
    try {
        secret = await getSecret('LT_SYNC_SECRET');
    } catch (e) {
        console.error('onMemberDeleted: could not read LT_SYNC_SECRET');
        return;
    }
    if (!secret) {
        console.error('onMemberDeleted: LT_SYNC_SECRET is empty');
        return;
    }
    for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
        try {
            const res = await fetch(`${BACKEND_URL}/api/v1/account/sync/wix-member-deleted`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Wix-Sync-Secret': secret },
                body: JSON.stringify({ member_id: memberId }),
            });
            if (res.ok) {
                const data = await res.json().catch(() => ({}));
                console.log('onMemberDeleted: backend status', data.status);
                return;
            }
            // 4xx other than 429 will not succeed on retry (bad secret, bad id).
            if (res.status < 500 && res.status !== 429) {
                console.error('onMemberDeleted: backend rejected the request', res.status);
                return;
            }
            console.warn('onMemberDeleted: backend error', res.status, 'attempt', attempt);
        } catch (e) {
            console.warn('onMemberDeleted: request failed, attempt', attempt);
        }
        if (attempt < ATTEMPTS) await sleep(1000 * attempt);
    }
    console.error('onMemberDeleted: gave up; run a manual reconcile for member', memberId);
}
