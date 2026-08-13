/**
 * Wix Velo Page Code for /api-key (Members Only)
 *
 * Completes desktop SSO by posting the API key to the backend handoff endpoint
 * (preferred) and optionally trying a localhost redirect (often blocked by Wix).
 *
 * SETUP:
 * 1. Page slug api-key, Members Only
 * 2. Elements: apiKeyText, copyButton, statusText, completeSignInLink (Link)
 * 3. Backend: api-key.web.js with getApiKeyForMember + completeDesktopHandoff
 * 4. Publish
 */

import wixLocationFrontend from 'wix-location-frontend';
import { currentMember } from 'wix-members-frontend';
import wixWindowFrontend from 'wix-window-frontend';
import { getApiKeyForMember, syncMemberToBackend, completeDesktopHandoff } from 'backend/api-key.web';

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

function showCompleteSignInFallback(finalUrl) {
    try {
        const lnk = $w('#completeSignInLink');
        if (lnk) {
            lnk.link = finalUrl;
            lnk.target = '_blank';
            lnk.text = 'Click here to finish signing in to the app';
            lnk.show();
        }
    } catch (e) { /* Link element may not exist */ }
}

function showKey(apiKey) {
    try {
        $w('#apiKeyText').text = apiKey;
        $w('#apiKeyText').show();
        $w('#copyButton').show();
    } catch (e) { setStatus('Signed in. Return to the Live Translate app.'); }
}

function isValidRedirectUri(uri) {
    try {
        const url = new URL(uri);
        if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') {
            return url.protocol === 'http:';
        }
        if (url.protocol !== 'https:') return false;
        return TRUSTED_HOSTS.some(host =>
            url.hostname === host || url.hostname.endsWith('.' + host)
        );
    } catch {
        return false;
    }
}

$w.onReady(async function () {
    try {
        setStatus('Signing you in…');
        try { $w('#apiKeyText').hide(); $w('#copyButton').hide(); } catch (e) { /* ignore */ }

        const query = wixLocationFrontend.query;
        const redirectUri = query.redirect_uri;
        const desktopSessionQ = query.desktop_session;
        if (redirectUri && isValidRedirectUri(redirectUri)) {
            try { sessionStorage.setItem('live_translate_redirect_uri', redirectUri); } catch (e) { /* ignore */ }
        }
        if (desktopSessionQ && typeof desktopSessionQ === 'string' && desktopSessionQ.length >= 16) {
            try { sessionStorage.setItem('live_translate_desktop_session', desktopSessionQ); } catch (e) { /* ignore */ }
        }

        const member = await currentMember.getMember();
        if (!member || !member.loginEmail) {
            setStatus('Please log in to continue.');
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

        const storedSession = (typeof sessionStorage !== 'undefined')
            ? sessionStorage.getItem('live_translate_desktop_session') : null;
        const desktopSession = desktopSessionQ || storedSession;
        if (storedSession) {
            try { sessionStorage.removeItem('live_translate_desktop_session'); } catch (e) { /* ignore */ }
        }

        // Preferred path: backend handoff — desktop polls Railway (no localhost needed)
        if (desktopSession && desktopSession.length >= 16) {
            setStatus('Completing sign-in to the desktop app…');
            const handoff = await completeDesktopHandoff(desktopSession, apiKey);
            if (handoff && handoff.success) {
                setStatus('Signed in! You can close this tab and return to Live Translate.');
                showKey(apiKey);
                return;
            }
            setStatus((handoff && handoff.error) || 'Handoff failed — trying fallback…');
        }

        const storedUri = (typeof sessionStorage !== 'undefined')
            ? sessionStorage.getItem('live_translate_redirect_uri') : null;
        const uriForRedirect = redirectUri || storedUri;
        if (storedUri) {
            try { sessionStorage.removeItem('live_translate_redirect_uri'); } catch (e) { /* ignore */ }
        }

        if (uriForRedirect && isValidRedirectUri(uriForRedirect)) {
            const separator = uriForRedirect.includes('?') ? '&' : '?';
            const finalUrl = `${uriForRedirect}${separator}api_key=${encodeURIComponent(apiKey)}`;
            setStatus('Almost done — click the link below if the app is still waiting.');
            showKey(apiKey);
            showCompleteSignInFallback(finalUrl);
            // Native navigation often works when wixLocationFrontend.to(localhost) does not
            setTimeout(() => {
                try {
                    if (typeof window !== 'undefined') {
                        window.location.href = finalUrl;
                    } else {
                        wixLocationFrontend.to(finalUrl);
                    }
                } catch (e) {
                    setStatus('Click the link above to finish signing in to the app.');
                }
            }, 400);
            return;
        }

        setStatus('Signed in. Return to the Live Translate app — it should finish automatically.');
        showKey(apiKey);
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
