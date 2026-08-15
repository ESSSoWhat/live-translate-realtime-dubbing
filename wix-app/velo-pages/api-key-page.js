/**
 * Wix Velo Page Code for /api-key (Members Only)
 *
 * Completes desktop SSO via storeDesktopHandoff(session_id), with localhost
 * redirect as fallback when no session_id is present.
 */

import wixLocationFrontend from 'wix-location-frontend';
import { currentMember } from 'wix-members-frontend';
import wixWindowFrontend from 'wix-window-frontend';
import { getApiKeyForMember, syncMemberToBackend, storeDesktopHandoff } from 'backend/api-key.web';

const TRUSTED_HOSTS = ['localhost', '127.0.0.1', 'livetranslate.app', 'www.livetranslate.app', 'livetranslate.net', 'www.livetranslate.net'];

function setStatus(msg) {
    try { $w('#statusText').text = msg; } catch (e) { /* element may not exist */ }
}

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

function isValidRedirectUri(uri) {
    try {
        const url = new URL(uri);
        if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return url.protocol === 'http:';
        if (url.protocol !== 'https:') return false;
        return TRUSTED_HOSTS.some(host => url.hostname === host || url.hostname.endsWith('.' + host));
    } catch { return false; }
}

$w.onReady(async function () {
    try {
        setStatus('Loading your API key...');
        try { $w('#apiKeyText').hide(); $w('#copyButton').hide(); } catch (e) { /* ignore */ }

        const query = wixLocationFrontend.query;
        const redirectUri = query.redirect_uri;
        if (redirectUri && isValidRedirectUri(redirectUri)) {
            try { sessionStorage.setItem('live_translate_redirect_uri', redirectUri); } catch (e) { /* ignore */ }
        }
        const sessionId = query.session_id;
        if (sessionId) {
            try { sessionStorage.setItem('live_translate_session_id', sessionId); } catch (e) { /* ignore */ }
        }

        const member = await currentMember.getMember();
        if (!member || !member.loginEmail) {
            setStatus('Please log in to view your API key.');
            return;
        }
        const email = member.loginEmail;

        await syncMemberToBackend(email);
        const result = await getApiKeyForMember(email);
        if (!result.success || !result.apiKey) {
            setStatus(result.error || 'Could not retrieve API key. Please try again.');
            return;
        }
        const apiKey = result.apiKey;

        const storedSession = (typeof sessionStorage !== 'undefined') ? sessionStorage.getItem('live_translate_session_id') : null;
        const sessionForPost = query.session_id || storedSession;
        if (storedSession) {
            try { sessionStorage.removeItem('live_translate_session_id'); } catch (e) { /* ignore */ }
        }
        const storedUri = (typeof sessionStorage !== 'undefined') ? sessionStorage.getItem('live_translate_redirect_uri') : null;
        const uriForRedirect = query.redirect_uri || storedUri;
        if (storedUri) {
            try { sessionStorage.removeItem('live_translate_redirect_uri'); } catch (e) { /* ignore */ }
        }

        // Prefer backend handoff (Wix often blocks navigation to localhost).
        // Still attempt localhost redirect afterward so the browser "returns" when allowed.
        if (sessionForPost) {
            setStatus('Signing you in to the app...');
            const handoffResult = await storeDesktopHandoff(sessionForPost, apiKey);
            if (handoffResult && handoffResult.stored) {
                setStatus('✓ Signed in! Returning to the Live Translate app…');
                if (uriForRedirect && isValidRedirectUri(uriForRedirect)) {
                    const separator = uriForRedirect.includes('?') ? '&' : '?';
                    const finalUrl = `${uriForRedirect}${separator}api_key=${encodeURIComponent(apiKey)}`;
                    setTimeout(() => {
                        wixLocationFrontend.to(finalUrl);
                        setTimeout(() => showCompleteSignInFallback(finalUrl), 1500);
                    }, 300);
                }
                return;
            }
            setStatus('Could not sign in automatically. Copy your API key below and paste it into the app.');
            showKey(apiKey);
            try {
                $w('#copyButton').onClick(() => {
                    wixWindowFrontend.copyToClipboard(apiKey);
                    setStatus('API key copied!');
                });
            } catch (e) { /* ignore */ }
            return;
        }

        if (uriForRedirect && isValidRedirectUri(uriForRedirect)) {
            const separator = uriForRedirect.includes('?') ? '&' : '?';
            const finalUrl = `${uriForRedirect}${separator}api_key=${encodeURIComponent(apiKey)}`;
            setStatus('Signing you in...');
            setTimeout(() => {
                wixLocationFrontend.to(finalUrl);
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
