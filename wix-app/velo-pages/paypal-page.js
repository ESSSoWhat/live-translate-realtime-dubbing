/**
 * Wix Velo Page Code for the Pricing / Upgrade page (e.g. /upgrade).
 *
 * Wires PayPal checkout buttons to the backend and shows the member's live usage.
 *
 * SETUP INSTRUCTIONS:
 * 1. Add the backend module (paypal.web.js) to your site's backend folder.
 * 2. Add LT_SYNC_SECRET to Wix Secrets Manager (only needed for the usage panel).
 * 3. On the page add these elements (IDs must match):
 *    - Buttons:  #paypalStarterBtn, #paypalProBtn, #paypalEarlyBtn
 *    - Status text:  #paypalStatus
 *    - (optional usage) Text: #usageTier, #usageDubbing, #usageTts
 *    - (optional) Progress bar or box: #usageDubbingBar (its width is set 0–100)
 * 4. Set the page to "Members Only" so currentMember is available.
 * 5. Paste this code in the page's code panel.
 *
 * Flow: click → backend creates order/subscription → redirect to PayPal approve URL.
 * On return, PayPal appends ?token=<orderId> (one-time) → we capture and provision.
 * Subscriptions are activated by the backend webhook (BILLING.SUBSCRIPTION.ACTIVATED).
 */

import wixLocationFrontend from 'wix-location-frontend';
import { currentMember } from 'wix-members-frontend';
import {
    createPayPalOrder,
    createPayPalSubscription,
    capturePayPalOrder,
    getPayPalConfig,
    getUsageForMember,
} from 'backend/paypal.web';

function setStatus(msg) {
    try { $w('#paypalStatus').text = msg; $w('#paypalStatus').show(); } catch (e) { /* element may not exist */ }
}

function setEnabled(enabled) {
    ['#paypalStarterBtn', '#paypalProBtn', '#paypalEarlyBtn'].forEach(id => {
        try { enabled ? $w(id).enable() : $w(id).disable(); } catch (e) { /* ignore */ }
    });
}

async function getEmail() {
    const member = await currentMember.getMember();
    return member && member.loginEmail ? member.loginEmail : null;
}

async function startSubscription(tier) {
    const email = await getEmail();
    if (!email) { setStatus('Please log in first.'); return; }
    setEnabled(false);
    setStatus('Redirecting to PayPal…');
    const res = await createPayPalSubscription(email, tier);
    if (res.success && res.approveUrl) {
        wixLocationFrontend.to(res.approveUrl);
    } else {
        setEnabled(true);
        setStatus(res.error || 'Could not start subscription. Please try again.');
    }
}

async function startOneTime(tier) {
    const email = await getEmail();
    if (!email) { setStatus('Please log in first.'); return; }
    setEnabled(false);
    setStatus('Redirecting to PayPal…');
    const res = await createPayPalOrder(email, tier);
    if (res.success && res.approveUrl) {
        wixLocationFrontend.to(res.approveUrl);
    } else {
        setEnabled(true);
        setStatus(res.error || 'Could not start payment. Please try again.');
    }
}

async function handleReturnFromPayPal() {
    // PayPal one-time redirect returns with ?token=<orderId>
    const query = wixLocationFrontend.query || {};
    const orderId = query.token;
    if (!orderId) return false;
    setStatus('Confirming your payment…');
    const res = await capturePayPalOrder(orderId);
    if (res.success && res.status === 'COMPLETED') {
        setStatus('Payment complete — your plan is now active!');
    } else if (res.success) {
        setStatus(`Payment status: ${res.status}. It may take a moment to activate.`);
    } else {
        setStatus(res.error || 'We could not confirm your payment. Please contact support.');
    }
    return true;
}

async function loadUsage() {
    const email = await getEmail();
    if (!email) return;
    const res = await getUsageForMember(email);
    if (!res.success || !res.usage) return;
    const u = res.usage;
    try { $w('#usageTier').text = `Plan: ${(res.tier || 'free').toUpperCase()}`; } catch (e) { /* ignore */ }
    try {
        const usedMin = Math.floor((u.dubbing_seconds_used || 0) / 60);
        const limitMin = Math.floor((u.dubbing_seconds_limit || 0) / 60);
        $w('#usageDubbing').text = `Dubbing: ${usedMin} / ${limitMin} min this month`;
    } catch (e) { /* ignore */ }
    try {
        $w('#usageTts').text = `TTS: ${(u.tts_chars_used || 0).toLocaleString()} / ${(u.tts_chars_limit || 0).toLocaleString()} chars`;
    } catch (e) { /* ignore */ }
    try {
        const pct = Math.min(100, Math.round(((u.dubbing_seconds_used || 0) / Math.max(u.dubbing_seconds_limit || 1, 1)) * 100));
        $w('#usageDubbingBar').style.width = `${pct}%`;
    } catch (e) { /* ignore */ }

    // 80% "upgrade" nudge — peak usage across metered features (skip unlimited tier).
    showUpgradeNudge(u);
}

function ratio(used, limit) {
    if (!limit || limit >= 2147483647) return 0;
    return Math.min(1, (used || 0) / limit);
}

function showUpgradeNudge(u) {
    const peaks = [
        { key: 'minutes', r: ratio(u.dubbing_seconds_used, u.dubbing_seconds_limit) },
        { key: 'text-to-speech', r: ratio(u.tts_chars_used, u.tts_chars_limit) },
        { key: 'translation', r: ratio(u.translation_chars_used, u.translation_chars_limit) },
    ];
    const peak = peaks.reduce((a, b) => (a.r >= b.r ? a : b));
    try {
        if (peak.r >= 0.8) {
            const pct = Math.round(peak.r * 100);
            const msg = peak.r >= 1
                ? `You've used all your ${peak.key} this month — upgrade to keep going.`
                : `You've used ${pct}% of your ${peak.key} this month — upgrade for more.`;
            $w('#upgradeNudge').text = msg;
            $w('#upgradeNudge').show();
        } else {
            $w('#upgradeNudge').hide();
        }
    } catch (e) { /* #upgradeNudge element optional */ }
}

$w.onReady(async function () {
    try { $w('#paypalStarterBtn').onClick(() => startSubscription('starter')); } catch (e) { /* ignore */ }
    try { $w('#paypalProBtn').onClick(() => startSubscription('pro')); } catch (e) { /* ignore */ }
    try { $w('#paypalEarlyBtn').onClick(() => startOneTime('early_adopters')); } catch (e) { /* ignore */ }

    // Hide PayPal buttons if the backend has no PayPal configured.
    try {
        const cfg = await getPayPalConfig();
        if (!cfg.configured) {
            setEnabled(false);
            setStatus('Card / PayPal checkout is not available right now.');
        }
    } catch (e) { /* ignore */ }

    const returned = await handleReturnFromPayPal();
    await loadUsage();
    if (returned) {
        // Refresh usage shortly after a successful capture.
        setTimeout(loadUsage, 2000);
    }
});
