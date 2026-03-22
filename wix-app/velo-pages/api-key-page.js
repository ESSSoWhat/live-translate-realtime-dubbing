/**
 * Wix Velo Page Code for /account/api-key
 *
 * This code handles the SSO flow for the Live Translate desktop app.
 *
 * SETUP INSTRUCTIONS:
 * 1. Create a new page (slug e.g. api-key → /api-key) in Wix Editor
 * 2. Add a text element with ID "apiKeyText" to display the API key
 * 3. Add a button with ID "copyButton" to copy the key
 * 4. Add a text element with ID "statusText" for status messages
 * 5. Add a Link element with ID "completeSignInLink" (initially hidden) — fallback when redirect is blocked
 * 6. Set the page to "Members Only" in page settings
 * 7. Paste this code in the page's code panel (replace any existing code)
 * 8. Add the backend module (api-key.web.js) to your backend folder
 * 9. Add WIX_SYNC_SECRET to Wix Secrets Manager
 */

import wixLocationFrontend from 'wix-location-frontend';
import { currentMember } from 'wix-members-frontend';
import wixWindowFrontend from 'wix-window-frontend';
import { getApiKeyForMember, syncMemberToBackend } from 'backend/api-key.web';

// Trusted redirect hosts (for security)
const TRUSTED_HOSTS = [
    'localhost',
    '127.0.0.1',
    'livetranslate.app',
    'www.livetranslate.app',
    'livetranslate.net',
    'www.livetranslate.net',
];

function setStatus(msg) {
    try { $w('#statusText').text = msg; } catch (e) { /* element may not exist */ }
}

/**
 * Show the completeSignInLink fallback when redirect may have been blocked.
 * Call this after attempting wixLocationFrontend.to() or if redirect fails.
 */
function showCompleteSignInFallback(finalUrl) {
    try {
        const lnk = $w('#completeSignInLink');
        if (lnk) {
            lnk.link = finalUrl;
            lnk.text = 'Click here if the app didn\'t sign in';
            lnk.show();
            setStatus('If the app didn\'t open, click the link above.');
        }
    } catch (e) { /* Link element may not exist */ }
}

function showKey(apiKey) {
    try {
        $w('#apiKeyText').text = apiKey;
        $w('#apiKeyText').show();
        $w('#copyButton').show();
    } catch (e) { setStatus('API key: ' + apiKey); }
}

$w.onReady(async function () {
    try {
        setStatus('Loading your API key...');
        try { $w('#apiKeyText').hide(); $w('#copyButton').hide(); } catch (e) { /* ignore */ }

        // Store redirect_uri early — Wix login redirect may strip query params when returning
        const query = wixLocationFrontend.query;
        const redirectUri = query.redirect_uri;
        if (redirectUri && isValidRedirectUri(redirectUri)) {
            try {
                sessionStorage.setItem('live_translate_redirect_uri', redirectUri);
            } catch (e) { /* ignore */ }
        }

        // Get current member
        const member = await currentMember.getMember();

        if (!member || !member.loginEmail) {
            setStatus('Please log in to view your API key.');
            return;
        }

        const email = member.loginEmail;

        // Sync tier first — auto-provisions user + API key if new
        await syncMemberToBackend(email);

        // Get API key from backend (via web module)
        const result = await getApiKeyForMember(email);

        if (!result.success || !result.apiKey) {
            setStatus(result.error || 'Could not retrieve API key. Please try again.');
            return;
        }

        const apiKey = result.apiKey;

        // Check for redirect_uri: in query (direct load) or sessionStorage (returned from login)
        const storedUri = (typeof sessionStorage !== 'undefined') ?
            sessionStorage.getItem('live_translate_redirect_uri') : null;
        const uriForRedirect = query.redirect_uri || storedUri;
        if (storedUri) {
            try { sessionStorage.removeItem('live_translate_redirect_uri'); } catch (e) { /* ignore */ }
        }

        if (uriForRedirect && isValidRedirectUri(uriForRedirect)) {
            const separator = uriForRedirect.includes('?') ? '&' : '?';
            const finalUrl = `${uriForRedirect}${separator}api_key=${encodeURIComponent(apiKey)}`;
            setStatus('Signing you in...');
            // Try automatic redirect first
            setTimeout(() => {
                wixLocationFrontend.to(finalUrl);
                // Fallback: show completeSignInLink in case redirect is blocked (e.g. popup blockers)
                setTimeout(() => showCompleteSignInFallback(finalUrl), 1500);
            }, 300);
            return;
        }

        showKey(apiKey);
        setStatus('Copy this API key to use in the Live Translate app:');
        try {
            $w('#copyButton').onClick(() => {
                wixWindowFrontend.copyToClipboard(apiKey);
                setStatus('API key copied!');
            });
        } catch (e) { /* ignore */ }

    } catch (error) {
        console.error('Error loading API key page:', error);
        setStatus('An error occurred. Please refresh the page.');
    }
});

/**
 * Validate redirect URI for security
 * Only allow localhost (for desktop app) and trusted domains
 */
function isValidRedirectUri(uri) {
    try {
        const url = new URL(uri);

        // Allow localhost (for desktop app callback)
        if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') {
            // Only allow http for localhost (not https required)
            return url.protocol === 'http:';
        }

        // For other hosts, require https and check against whitelist
        if (url.protocol !== 'https:') {
            return false;
        }

        return TRUSTED_HOSTS.some(host =>
            url.hostname === host || url.hostname.endsWith('.' + host)
        );

    } catch {
        return false;
    }
}
