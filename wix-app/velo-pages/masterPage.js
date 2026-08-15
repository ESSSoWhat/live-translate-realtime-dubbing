/**
 * Global site code — Live Translate desktop SSO helpers.
 * Copy into Wix site `src/pages/masterPage.js` (or Editor Site Code).
 *
 * Live /app-auth is often a static marketing page (Editor/Git page code not bound).
 * Catch session_id + redirect_uri on ANY page, persist them, and forward to /api-key.
 */

import { authentication } from 'wix-members-frontend';
import wixLocationFrontend from 'wix-location-frontend';

const SESSION_KEY = 'live_translate_session_id';
const REDIRECT_KEY = 'live_translate_redirect_uri';
const SSO_RETURN_KEY = 'live_translate_sso_return_url';

function isValidReturnUrl(url) {
    if (typeof url !== 'string') return false;
    const decoded = decodeURIComponent(url);
    return decoded.startsWith('/') && !decoded.startsWith('//');
}

function isValidRedirectUri(uri) {
    try {
        const url = new URL(uri);
        if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') {
            return url.protocol === 'http:';
        }
        if (url.protocol !== 'https:') return false;
        const trusted = [
            'livetranslate.app',
            'www.livetranslate.app',
            'livetranslate.net',
            'www.livetranslate.net',
        ];
        return trusted.some((h) => url.hostname === h || url.hostname.endsWith('.' + h));
    } catch {
        return false;
    }
}

function persistDesktopSsoQuery(query) {
    const sessionId = query.session_id;
    const redirectUri = query.redirect_uri;
    let saved = false;
    if (sessionId && typeof sessionId === 'string') {
        try {
            sessionStorage.setItem(SESSION_KEY, sessionId);
            saved = true;
        } catch (e) { /* ignore */ }
    }
    if (redirectUri && isValidRedirectUri(redirectUri)) {
        try {
            sessionStorage.setItem(REDIRECT_KEY, redirectUri);
            saved = true;
        } catch (e) { /* ignore */ }
    }
    return { saved, sessionId, redirectUri };
}

function buildApiKeyTarget(sessionId, redirectUri) {
    const params = [];
    if (sessionId && typeof sessionId === 'string') {
        params.push('session_id=' + encodeURIComponent(sessionId));
    }
    if (redirectUri && isValidRedirectUri(redirectUri)) {
        params.push('redirect_uri=' + encodeURIComponent(redirectUri));
    }
    return params.length ? `/api-key?${params.join('&')}` : '/api-key';
}

$w.onReady(function () {
    try {
        const query = wixLocationFrontend.query;
        const path = (wixLocationFrontend.path || []).join('/') || '';
        const onApiKey = path === 'api-key' || path.endsWith('/api-key');

        const ssoReturn = query.sso_return;
        if (ssoReturn && typeof ssoReturn === 'string') {
            const decoded = decodeURIComponent(ssoReturn);
            if (decoded.startsWith('/') && !decoded.startsWith('//')) {
                sessionStorage.setItem(SSO_RETURN_KEY, decoded);
            }
        }

        const { saved, sessionId, redirectUri } = persistDesktopSsoQuery(query);
        if (saved && !onApiKey && (sessionId || redirectUri)) {
            wixLocationFrontend.to(buildApiKeyTarget(sessionId, redirectUri));
            return;
        }

        let returnUrl = query.returnUrl;
        if (!returnUrl && typeof sessionStorage !== 'undefined') {
            returnUrl = sessionStorage.getItem(SSO_RETURN_KEY);
        }
        if (returnUrl && isValidReturnUrl(returnUrl)) {
            authentication.onLogin(() => {
                try {
                    if (typeof sessionStorage !== 'undefined') {
                        sessionStorage.removeItem(SSO_RETURN_KEY);
                    }
                } catch (e) { /* ignore */ }
                wixLocationFrontend.to(returnUrl);
            });
        }
    } catch (e) {
        /* ignore */
    }
});
